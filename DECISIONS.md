# Decisions in force

The decisions about the agent loop that hold now, each with why and the date it took its current form. Edit an entry in
place when a decision changes: rewrite it, give it the new date, and say in the commit what changed and why. Git keeps
the old version; this file keeps only the current one. What is still to do is an issue in Linear, team OWL; a new task
goes to its Triage. The loop's numbers in these entries come from the product that ran the loop before it moved here.

Read the section you are about to change before you propose or build against it. If a request goes against an entry here,
say so and ask the owner first.

### Tooling
- **uv manages Python, and the lock is committed** (2026-09-27). Python 3.14 for the tests; the scripts that the owner
  runs as root or before a uv install run on the Mac's `/usr/bin/python3` (3.9) or `/usr/local/bin/python3` (3.11 or
  newer), as each script's docstring says.
- **Ruff for lint, no formatter; mypy strict** (2026-09-25/26).
- **A script with logic is one Python file, standard library only; shell only for lists of commands** (2026-09-27).
- **mise pins the tools and holds the everyday commands** (2026-10-08).

### Delivery
- **Agents work from issues, under one merge bar and three review rounds** (2026-10-22, the owner, after reviews took up
  to six rounds each and every hand-off went through the owner). A review finding blocks a merge only when it meets the
  merge bar (AGENTS.md "Autonomous runs"); the rest becomes a follow-up issue. A pull request still blocked after round 3
  goes to the owner as a decision (two rounds until 2026-11-16: the owner's call). The roles' briefs are agent-agnostic
  (`agents/`): the harness, the model and the effort per role are settings, so an open-weight implementer and a Claude
  reviewer can share one workflow. The tools: GitHub Pro (the ruleset), two GitHub Apps, Linear as the record,
  Namespace's runners. Orchestrator.inc was dropped on 2026-10-24: asked for ten tasks it ran ten agents at once,
  crashed, and had kept the runs' state only in itself, so every task started again. An orchestrator keeps no state of
  its own (agents/README.md "What the orchestrator trusts"), apart from the record of each session's host in its local
  state file.
- **The review runs on the ready mark, and an approve is final** (2026-10-24, the owner, after a measurement of 25 pull
  requests: a reviewed pull request took 8.4 h median for 21 min of work, 90% of the time waited for the owner's next
  session, 19 of 52 reviews and 21 commits came after a first approve, no pull request needed a third blocking round,
  and one review blocked on a finding it could not confirm). The reviewer runs on GitHub Actions when the pull request is
  marked ready for review, never on a push, with its own GitHub App's token, and posts a formal review that the ruleset
  counts. Non-blocking findings become follow-up lines, filed by the reviewer with the approve into the open follow-up
  issue of the pull request's Area (up to six findings of several reviews: the owner's call, 2026-11-16), under a floor
  (agents/README.md "Follow-ups"); nothing is pushed after an approve; an unconfirmed finding never blocks.
  - **`fix_now` and the fast lane** (2026-10-28, the owner's call, after a night of 35 pull requests: a median of 16
    minutes from open to merge, but 25 of 29 new issues filed by agents, 13 of the 14 that the owner decided accepted,
    and five follow-ups 4 to 6 hours in Triage while nothing ran). Round 1 asks for the cheap non-blocking fixes in the
    same pull request (`fix_now`: XS, in its own files, no new choice; on a docs-only pull request any size since
    2026-11-03, the owner's call after a follow-up held two small fixes of its own documents). A follow-up past depth 1
    holds only merge-bar findings. An issue that an agent files one step from an owner's Todo, at any depth of follow-up
    and in any Area, XS or S, with no change that is the owner's (agents/README.md "The fast lane" rule 4) and nothing
    for the owner, goes straight to Todo with the label `fast-lane` (widened by the owner on 2026-11-09: 93 of 96
    decided issues that went to Triage were accepted, and no group was under 94%). There is no daily cap, by the owner's
    choice: a cap does not predict which issues the owner would cancel, and the one-step rule bounds the volume. Every
    other issue an agent files goes to Triage.
  - **Every review finding is acted on or not written**: blocking, `fix_now` or a follow-up line (the owner's call,
    2026-10-28: a finding left "in the review's text" was read by nobody, and that bucket hid two round-2 findings that
    each said a document was wrong). The record is Linear, and nothing about a review goes into a file on the branch.
  - **The ruleset and the code owners.** The ruleset `main` requires each product's gates and the check `verdict` (not
    an up-to-date branch: pull requests merge in parallel, below) and the code owners' review, with the owner on the
    bypass list. `.github/CODEOWNERS` names the owner for the guards (the files that check the agents' work, run beside
    a key, or ship), and the implementer's app keeps no Workflows permission on purpose. Since 2026-10-28 (the owner's
    call: 10 of 35 pull requests of a night waited for the owner's merge, from 11 minutes to 5.6 hours, and 4 of them
    changed no guard) the workflow's prose (`agents/README.md`, `implementer.md`, `planner.md`) and DECISIONS.md merge
    on the verdict; each skill's `SKILL.md` stays the owner's, since its frontmatter can grant tools or declare hooks in
    the owner's session (the owner's call, 2026-10-28). A product's limits file (`limits.toml`) is not in CODEOWNERS: a
    pull request that changes or removes a value, or adds a key of the owner's list (`OWNER_LIMITS` and
    `OWNER_LIMIT_PREFIXES` in `.github/verdict.py`: the money, plan and sandbox limits), waits for the owner's merge,
    and every other change to the file merges on the verdict (the owner's call, 2026-11-04: a pull request waited 72
    minutes to add one new limit). AGENTS.md is not in CODEOWNERS either: a change merges on the verdict, except one
    that touches its section "Autonomous runs" or a `## ` heading, which waits for the owner (`section_owned` in
    `.github/verdict.py`; the owner's call, 2026-11-09: AGENTS.md was the reason for 22 of 69 owner's merges, and the
    only owned file in 6). A skill's other files and a doc check nothing, so they need no more trust than the code
    (the owner's call, 2026-11-23: of 92 pull requests that the owner merged in one week, 17 changed an owned file that
    is no guard). `orchestrator/deploy/` and `.workflow/agent_user.py` are not in CODEOWNERS either: a merge runs none of
    their code, which runs only when the owner runs a command (as root or as the owner), so the owner checks them when
    they run. The owned launcher `.workflow/approved_run.py` shows their commits and diff since the owner's last yes,
    asks one yes, records it in a file that only root can write, and runs only the bytes it showed; with no change it
    asks nothing (the owner's call, 2026-11-23: these files were in up to 36 of those 92 pull requests; `install.py`
    cannot check its own diff, so the launcher does). The review reads main's copies of its rules, so a pull request
    cannot change the rules of its own review, and the reviewer blocks a line here that names no owner's call or issue.
    `.github/linear.py` stays the owner's: the review job runs it beside the reviewer app's token.
  - **The setup.** The reviewer app's ID is an Actions variable and its key a secret (an ID is public; no secret in a
    file on the owner's machine, AGENTS.md "How to work here"). A Linear team is made by `python3 .github/linear.py setup
    TEAM`, and Linear's GitHub integration syncs issues one way, GitHub to Linear: Linear is the record, so nothing of it
    becomes a GitHub issue.
  - **Pull requests merge in parallel** (the owner's call, 2026-11-02): the ruleset does not need an up-to-date branch,
    because the serial merges cost a round of gates per pull request after every merge (a branch update by the workflow
    had given 23 of 35 pull requests of one night a commit after their approve). The owner accepts the small risk that
    two pull requests, reviewed and green apart, break together: the next pull request's gates test its branch merged
    with main, so the break shows within minutes, a product's gates run on main each night, and a red main is fixed
    first. GitHub's merge queue would test the combination, but it needs a repository owned by an organization.
  - **The workflow `branch-update` finds a conflict, and nothing else.** A conflict with main is the one thing that
    still needs a person: auto-merge waits on it for ever with nobody told. After each merge the workflow asks GitHub to
    merge main into the oldest approved pull request whose `mergeable` is CONFLICTING, GitHub refuses, and the conflict
    goes back to an implementer. It does not update a pull request that is only behind: that runs the gates again and
    makes the merges serial. The implementer app's token asks for the update and turns on auto-merge: GitHub merges as
    the account that turned auto-merge on, and a merge by GitHub Actions' token starts no workflow. GitHub accepts the
    app's update when it brings in a change to `.github/workflows/`, since GitHub writes the merge, so no third app is
    needed. The resolution of such a conflict is reviewed as round 1 again (the owner's call, 2026-10-28): a pull
    request approved in round 2 would otherwise reach a third round and wait for the owner for a mechanical step. A
    third ready mark without the owner's answer also moves the issue to Escalated. Failed gates on an approved head
    start the count again too (the owner's call, 2026-10-28), since main's change usually made them fail. An approved
    pull request that waits for the owner's merge (a code owner's file) is checked for a conflict too, after the
    auto-merge ones, and a conflict on it takes the same path: the comment, the label `escalated` off and the issue to
    In Progress, so the orchestrator wakes the session that opened the pull request (the owner's call, 2026-11-23). The
    owner only merges. A real commit after an approve gets a new review, whose verdict moves the issue out of Escalated
    (the owner's call, 2026-10-28): the approve no longer covers the code the owner would merge. That move answers
    nothing, so it starts no count: only the owner's answer does (a move from Escalated to Todo, Approved or Canceled),
    and the fix after such a review is round 2.
  - **The verdict is a required check, not an approval** (2026-10-25, the owner's call: GitHub counts an approval only
    from an account with push access, which the reviewer's app has not). The review workflow reports the check
    `verdict` on the reviewed head, success only for an approve. A push leaves the new head without it; a branch update
    (only automatic merges of main, whatever main changed) gets the approve's check with no review (the owner's call
    the same day: three re-reviews in one afternoon).
    The check holds only the review (the owner's call, 2026-11-25, OWL-30: the review job waited for the gates after the
    model, to fail the check on a red or skipped gate). Each gate is a required check of its own, a gate job never skips as
    a whole (GitHub counts a skipped required check as passed), and the workflow `gates-failed` sends an approved head with a
    gate that did not pass back to an implementer. The review job ends when the model ends.
  - **GitHub merges on the verdict** (2026-10-25, the owner's call: an approved pull request waited for the owner's
    click, and Linear is where the owner sees what waits). An approve turns on GitHub's auto-merge, and GitHub merges
    when the ruleset lets the pull request through. A pull request that changes a code owner's file gets no auto-merge
    on the reviewer app's approve: the owner merges it, and its issue goes to Escalated, the one state that waits on the
    owner, whose notifications come from Linear. The ruleset's code owner review did not hold it (three pull requests
    merged on the reviewer app's approve alone, the owner their author, 2026-10-26). The owner's label
    `owner-approved` is also the owner's merge (2026-11-05, the owner's call): on its head, auto-merge goes on for a
    code owner's file too, since the owner saw the head (two pull requests waited for a third click after the label).
    The merge is GitHub's, under the ruleset, or the owner's; no agent merges. The state was named Needs decision until
    the same day; "Blocked" was rejected, since it reads as blocked by another task.
- **Two states wait on the owner: Decide holds an issue with an open choice, Merge holds a pull request that waits for
  the owner's action** (2026-11-24). The rule takes effect in this repository (OWL-1). Until then, Escalated and the
  `Question:` issues stay as the entry above and agents/planner.md "The issue" say. The rule:
  - One feature is one issue. Its open choice is a section "Decision" in that issue: the question, the options, what
    each costs and a recommendation. This reverses the rule of 2026-11-23 that put each choice in a second issue,
    `Question: ...`, that blocked the work issue: one feature became two issues, and its record was in two places.
  - An issue with an open choice goes to Decide when it is filed, never to Triage. So a move from Triage to Todo is
    always safe. Before the `Question:` issues the question was in the issue in Triage, and the owner moved such issues
    to Todo without the answer: the run then stopped at once.
  - A run that needs a choice moves its issue to Decide with one comment that holds the same parts. A review whose
    verdict is `needs-decision` goes to Decide when its question is a choice (a finding that is the owner's call), and
    so does a pull request still blocked after the last review round. So Decide holds new and started issues.
  - The owner answers on the issue and moves it to Todo, Backlog or Canceled. That move is the answer. For a pull
    request in Decide, the owner can also add the label `owner-approved`, which accepts it as it is. The owner checks
    the size and the priority at the same time, since the answer can change the size. A run that finds no answer to
    the section "Decision" stops before it makes a worktree and moves the issue back to Decide.
  - Merge holds only an action on a pull request: an approve on a code owner's file, an approved workflow change, a
    workflow patch to apply, auto-merge that GitHub refused, failed gates on an approved head that are not the
    branch's fault, and the owner's step before the merge (a check of a result that the run made, never a choice).
    Escalated is renamed Merge. On GitHub the pull request carries the label of its state, `decide` or `merge`, in
    place of `escalated`.
  - Why: Escalated held both questions and merges, and the owner opened each pull request on GitHub to see which. Each
    state's name is a verb: it says what the owner does in that column. Rejected: a label `needs-decision` (it left the
    issue in Triage or Todo, where a quick move still started a run, and gave the owner no column to work through), and
    the name "Undecided" (it describes the issue, not the owner's action).
- **Our own orchestrator starts the runs, and opens each one in a session host** (2026-11-09, the owner's call). The
  tools work on two layers: a trigger that turns an issue into a run (Cyrus, Linear's own coding agent, Codex, Cursor,
  Devin, the Copilot coding agent) and a session host where a person watches and steers the runs (herdr, Paseo, T3
  Code). A trigger tool runs its agents headless and owns its sessions, so no session host shows them. The owner wants
  each run in the same place as the owner's own sessions, to answer it or take it over. So the orchestrator is ours:
  one process, standard library, Linear and GitHub as its only state. It opens each run through one small seam, one
  adapter for each host. The orchestrator is the standard part, the same for every person; the session host is a
  local, personal choice, like a code editor or a dashboard, and affects nobody else (the owner's call, 2026-11-11).
  The orchestrator supports herdr, Paseo and a detached process (a T3 Code adapter is not built). Each developer picks
  a host in their own `orchestrator.toml` (`session_host`) and can change it at any time, with no wait for the agents
  that run: the old host keeps its sessions' wakes and closes (the owner's call, 2026-11-23). The record of each
  session's host is in the local state file, not in Linear: the host is personal. The current host and harness pairs
  are herdr with Claude Code or pi, Paseo with Claude Code or pi, and a detached process with Claude Code. The
  orchestrator builds the start command from the host and the harness of `inference_profile`, not from a config
  command (the owner's call, 2026-11-22). For a harness that takes a permission mode, the host uses the inference
  profile's `permission_mode` ("Claude Code runs in `auto` mode" below). Linear needs no integration beyond the reads
  and moves of `.github/linear.py`, and one comment that the orchestrator keeps itself: the queue, in one comment of a
  pinned `hands-off` issue (`queue_issue`), edited in place, for Linear's app on the phone (the owner's call,
  2026-11-10). So the phone needs no app of ours: the issue is the record, the session host is the view of a run, and
  the pinned issue is the view of the queue.
  - **The owner's check is the install, not the merge** (the owner's call, 2026-11-10: a click per pull request would
    make the owner the limit of the night runs). `orchestrator/` merges on the verdict and has its own Area,
    `orchestrator`. The service never runs the checkout: it runs a copy that the owner installs, after the install
    prints the commits and a dry round of the new and the installed code side by side. It runs as the agents' user,
    never in the owner's account, since its code is agent-written, from a service file and a copy that the agents' user
    cannot write (`orchestrator/deploy/`, which the owner checks when it runs, through the owned launcher
    `.workflow/approved_run.py`). It holds no permission that the agents lack, so a merged change cannot widen its
    power; more is a new owner's decision. An update from main with no install was rejected: a broken merge at night
    would stop or misdirect the loop, and nothing but the orchestrator starts the run that fixes it. The code names no
    product (a config file holds the team, the repository and the commands) and no platform outside its service files,
    so it can move from the Mac to a VPS.
  - **The fix round stays in the session that opened the pull request** (the owner's call, 2026-11-10): that session
    reads the review and fixes it with the context it already has. The orchestrator wakes it: at the event (a request
    for changes, failed gates, the branch update's conflict) it sends that session one message, only when the host
    shows the session at rest (idle, or done on herdr: a pi pane shows done until viewed), never to a running one (the
    owner's idea, 2026-11-16: the sessions' own polling missed events). A fix that no session took within 5 minutes of
    its event gets one new session from the orchestrator, once for each wait (the owner's call, 2026-11-16: three pull
    requests waited for the owner's one sentence, one of them for 6.5 hours). It goes to Escalated with one comment
    when that session takes it neither within 5 minutes, and the owner moves the issue to Todo: the orchestrator then
    starts the run with its settings and can wake it later.
- **The agent loop moves to its own public repository, which names no product** (2026-11-23, the owner's calls). The
  name owlcode reads as "I'll code", and the owl works at night while the owner sleeps. The owner shows the loop in
  public, and the products that it builds stay private. So the whole loop lives here: the orchestrator, the review
  workflow and the other loop workflows, the scripts of `.github/` and `.workflow/`, the briefs, the skills, the hooks
  and the guards. A product uses it at a pinned commit (the next entry). The rules:
  - This repository is built for the owner, in public. It gives no support and takes no outside issue or pull request.
    It has no releases, no changelog and no package. This keeps it free of other people's use cases.
  - No file, commit message, branch name, pull request, review or comment here names a product of the owner: no name,
    no issue key of a product's team, no product word, no link to a product's issue, and no private repository. OWL keys
    and the workspace's neutral address (`linear.app/maurolopes`) are allowed (2026-11-24). A private repository that
    becomes public shows its whole past, so the rule holds from the first push.
  - The history starts fresh: one first commit, dated when it was made, from a tree that was already clean. A filtered
    history was rejected: every old version of a file and every commit message would become public, and one miss
    leaks. No commit gets a false date.
  - All the owner's projects share one GitHub account, `maurolopes`, and one Linear workspace (`linear.app/maurolopes`),
    with one team each (2026-11-24). Reasons: Linear's GitHub app installs once for each GitHub account, so a second
    workspace cannot connect, and this repository's merges could not move its issues to Done. The owner plans many
    products and most will not prove themselves, so a GitHub organization and a workspace for each one costs too much.
    Linear's comment on a pull request cannot be turned off, but its issue description can ("Include issue
    descriptions in comments" is off): the comment holds only the issue's key, title and link. A product's key in a
    pull request here would show that issue's title: the leak guard and the rule above cover it. A product that proves
    itself then moves to its own GitHub organization and workspace. Rejected: a workspace for this repository alone (no
    GitHub link), and a GitHub organization for each product (the cost).
  - A leak guard measures what is left (`mise run leak:count`). Its patterns are in no repository, and it prints no
    matched text.
- **A product takes the loop's files at a pinned commit, as a synced copy with a check** (2026-11-25, the owner's call,
  OWL-10). A product keeps the files of `.workflow/loop-files.txt` at the paths that the harnesses and the workflows
  read (`.claude/`, `.agents/`, `.pi/`, `.githooks/`, `.github/`), at one commit of this repository that it records. A
  bump task copies the files of a new commit and prints the diff since the old one: that diff is the owner's review of
  each new pin. A check in the product's gates refuses a copy that differs from this repository at the recorded
  commit, so a hand edit fails and a fix to the loop is a pull request here. Rejected: a checkout at the pin that the
  product points to (a submodule or a clone outside it): the harnesses read fixed paths in the worktree, so it needs
  links or a second copy anyway, and every worktree and workflow needs the second checkout. An installed package: the
  briefs, the skills and the hooks are files at fixed paths, not code.
- **Install and restart require an offline preflight of the active profile** (2026-11-22). A failed credential check or
  pi model lookup leaves the running service in place. The health check and each start use the same preflight. The
  Paseo daemon's profile is no failure there: the config is the one place of the profile, and the service applies it
  (the owner's call, 2026-11-23). It starts a daemon that does not run, and switches one on other profiles when no agent
  is in a turn, with the starts halted until then; only a daemon that cannot be read fails.
- **A setup failure does not count as an issue's try.** A failed start before a session exists halts new starts and
  fix escalations for all teams. Wakes, closes and queue updates continue. The service assigns one Linear notice to the
  owner. A passing preflight lets the service retry. A failure with the same cause does not send another notice during
  the same halt. If the host confirms the pending launch, starts resume without waiting for preflight. If confirmation
  fails, the banner tells the owner to inspect the workspace and fix the host error. The notice closes when the halt
  clears. An error after the session exists follows the session's own rules, except a Paseo agent that stops in
  `error` within `claim_wait_seconds` of its start before any claim: that is a setup failure too, and its agent is
  archived. That halt keeps its one notice open until the restarted start claims or its claim wait passes, so a repeat
  sends no second notice.
- **The implementer runs a local review before each pull request opens** (2026-11-18, the owner's call, after a
  measurement of 255 merged pull requests, 143 with the review and 112 without). Compare the size bands, not the raw
  share. The round-1 approve share was 58% with the review and 55% without, a gain of 3 points that is below the bar of
  10 points. But the reviewed pull requests are larger: 75 of 143 changed over 100 lines, against 26 of 112. Within
  each size band the review approves 12 to 28 points higher (100 lines or fewer: 79% against 67%; 101 to 400: 45%
  against 17%), and weighted to the same size mix the gain is about 19 points. The implementer fixed 213 of its 229
  findings (93%). It costs about $0.29 for each pull request. The review stays advisory: it gives no verdict and no
  check, and runs once, before the first ready mark (agents/implementer.md, agents/README.md "The local review",
  `.github/local_review.py`).
- **The implementer and the local review can run on a paid API** (2026-11-20, the owner's call: the Claude subscription
  neared its weekly limit). The orchestrator's `inference_profile` selects the provider for runs and local reviews. The
  GitHub reviewer stays on the Claude subscription. A local review on a different model family finds other faults.
- **A run is not tied to Claude Code: a profile names its harness, its provider and its models** (2026-11-20, the
  owner's call). The model catalog (`orchestrator/model-catalog.json`, the owner's idea of 2026-11-22) connects each
  profile to its harness and provider. It records the API style, address, credential, permission modes, model IDs,
  effort mappings, limits and prices with their sources and dates. The orchestrator validates each `[profiles.NAME]`
  table against it before a start. `agent_user.py` uses it to write pi's provider entries. The script still owns the
  harness install and review commands. The config names the runs' models and the local review's model and effort level
  (`review_model`, `review_effort`: the owner's call of 2026-11-21, since a review on another model than the run's
  finds other faults; `install` and `restart` write them beside the installed script, which reads no TOML on Python
  3.9). `session --profile NAME` (a pane's `agent` or `claude`) starts the profile's harness. Every key, credential,
  address or provider-switch variable that a harness's entry lists (Claude Code's: from its env-vars page) leaves the
  caller's environment first, so a caller's key never reaches a run. Claude Code and pi ship. A cell of a profile may
  name another `[profiles.NAME]` table as its third field: that table's provider, harness, permission mode and
  credential run the cell (the owner's idea of 2026-11-23: Claude Opus in Claude Code for the hard issues, a cheap paid
  key in pi for the easy ones). `inference_profile` stays the one switch, a preset of the whole mix. The local review
  uses one model for the whole mix: the review model of the profile in force, whatever profile runs the cell, so the
  reviews compare across the cells (the owner's call, 2026-11-23).
- **Ollama runs only on its OpenAI-compatible API: in pi, never in Claude Code** (2026-11-22, the owner's call). Ollama
  Cloud's Anthropic-compatible API ignores `cache_control`. The 2026-11-21 measurement found no cache hits in Claude Code
  and about 99% cache hits in pi on the OpenAI-compatible API. Use `pi-ollama` for runs and local reviews. The catalog
  no longer accepts Claude Code on Ollama.
- **Claude Code runs in `auto` mode** (the owner's calls). Claude Code's safety check for `auto` works only on
  Anthropic's API. The `claude` profile sets `permission_mode = "auto"`. The orchestrator passes this mode with
  `--permission-mode` for `claude` and `--mode` for `paseo run`.
- **A pi run asks for no permission** (under the owner's call above). pi has no permission modes. It runs each tool call
  without a question (pi's docs, "Run Pi safely"), so its table has no `permission_mode`. A prompt injection can use the
  keys of the agents' macOS user. That user has no server key or password manager session. The ruleset protects
  `main`, and the git hooks run. The guards (no edit in the main checkout, no kill by name) reach pi as an extension,
  `.agents/guards/pi.ts`, which `.pi/settings.json` names. pi trusts no project by itself, and loads a project's own
  files (`.agents/skills`, `.pi/`) only in a project that it trusts, while a run cannot answer its question:
  `agent_user.py user` marks the agents' clones trusted in `~/.pi/agent/trust.json`, and no other folder, so a
  repository that a run clones elsewhere loads none of its own extensions. The local review runs with `--no-approve`
  and only pi's read-only tools: `--no-approve` keeps out the files that need trust (`.pi/settings.json`, `.pi/mcp.json`,
  `.pi/` extensions, skills, prompts, themes and system prompts, and `.agents/skills`), while the context files
  (AGENTS.md, CLAUDE.md) still load (pi's docs, security.md).
- **OpenCode was dropped** (2026-11-24). The loop runs on Claude Code and pi only. OpenCode was added for one reason: a
  comparison with pi on Ollama Cloud, and nobody measured it. Each harness costs work on every workflow change
  (adapter, guards, speed lines, preflight, catalog, docs), and an OpenCode run had no guards. `agent_user.py user`
  removes an OpenCode that an earlier run installed. Do not add it back without a new reason, such as a measured gain
  that pi and Claude Code cannot give.
- **The runs can use the owner's ChatGPT subscription, and pi keeps that sign-in in its own file** (2026-11-21, the
  owner's call). The profile `pi-chatgpt` runs pi on pi's own provider `openai-codex`, which signs itself in: a run
  costs nothing beyond the subscription the owner already pays, and its requests count against the ChatGPT plan's
  limits, which the owner's own use of ChatGPT and Codex shares, so a run that hits them stops like a run out of
  credit. pi's `/login` (the owner's, as the agents' user) writes the sign-in to `~/.pi/agent/auth.json` in that user's
  home, mode 600, and pi renews it itself. That file is the exception to "no key in a file on the owner's machine": it
  holds no key that the owner typed, only the token that pi renews, in a home that is that user's alone, and the
  agents' user already exposes its keychain to every run (a run's prompt injection can read the Linear key, the app's
  key and the Claude token), so the file adds little risk. A wrapper that wrote the file before a session and read
  refreshed tokens back into the keychain was not taken: it copies the token outside pi's own lock and fights pi's
  refresh. Nothing copies, prints or reads that token: `agent_user.py pi-signin` and the profile's `key_check` test
  that the file exists, that others cannot read it and that it holds an `openai-codex` entry, and
  `owlcode check` prints `its sign-in is there`. Several runs share the one sign-in: pi refreshes it inside
  `credentials.modify` under a lock on the file, and the refresh re-reads the entry first and skips when another
  session renewed it (measured in pi 1.5.2's source, `dist/core/auth-storage.js` and `pi-ai/dist/auth/resolve.js`), so
  no `max_runs` limit and no lock of ours. Its local review runs on the same provider, with `gpt-6.1-sol`.
- **LithosAI is one more inference provider, and its thinking levels are its own** (2026-11-22, the owner's call). The
  profile `pi-lithosai` runs pi on LithosAI's OpenAI-compatible Chat Completions API (`https://api.lithosai.cloud/v1`)
  with a key of the agents' own: the Keychain item `lithosai-key`, optional since the owner may switch later, and
  stored without a shape check because its format is not measured. The runs use `deepseek-ai/DeepSeek-V4.1-Flash` at
  `high`, the owner's choice of the same day (the Base tier: $0.15 input, $0.003 cached input and $1.20 output per
  million tokens, a promo of $0.60, lithosai.com/pricing), one of the models that `pi-ollama` runs, so the two
  providers' cost and speed compare on one model; the local review uses `zai-org/GLM-5.3-Flash` at `max`, the owner's
  choice of the same day (the Base tier: $0.15 input, $0.03 cached input and $0.50 output per million tokens) and its
  page's default for a request that names no effort, another family than the runs'. The catalog and pi offer only
  `low`, `high` and `max` for these models. LithosAI runs GLM's `minimal`, `medium` and `xhigh` as `max`. The catalog
  disables these aliases so a cheaper-sounding level does not run at full cost (the owner's call). The credit is
  prepaid, and every key of an organisation draws on the same balance and the same rate limits. LithosAI states that
  it is a zero-data-retention company: it keeps no input or output after processing (lithosai.com/privacy-policy, the
  owner's reading of 2026-11-22); the page does not address training. `user` writes the provider into
  `~/.pi/agent/models.json` with every tier's ID, the context window, the largest output and the catalog's thinking
  levels, since pi gives a model that its entry does not list no thinking level at all.
- **The loop's jobs run on Namespace's runners or on GitHub's, each by its length** (2026-10-23, the owner's call, in
  place of Blacksmith's): GitHub's included Actions minutes ran out (1,801 of 2,000 used by 2026-10-19). A product's
  long jobs (its gates, its caches' builds) name `namespace-profile-maurolopes` (4 vCPU, 8 GB). The review's `review`
  and the watchdog's `watch` read the repository variables `LOOP_REVIEW_RUNNER` and `LOOP_WATCH_RUNNER`, with
  `ubuntu-latest` as the default: the runner is the repository's choice (OWL-20, the owner's call, 2026-11-25: the
  first review on this repository waited in `queued` for a Namespace runner that the repository could not use, and a
  public repository does not name the owner's profile). The owner sets `LOOP_REVIEW_RUNNER` to `namespace-profile-2c4g`
  (2 vCPU, 4 GB, since 2026-11-12, the owner's call) in each repository that uses Namespace: a review mostly waits for the
  model, and it was 25% of the Namespace unit minutes of one day. Four hand reviews of one pull request measured it:
  2 min 8 s and 2 min 38 s on 4 vCPU, 2 min 48 s and 2 min 13 s on 2 vCPU (5% longer on average, all but 17 to 20 s in
  the model's step), so a review costs about half the unit minutes. The short jobs (seconds of `gh`, `git` and the
  standard-library scripts under `.github/`) run on `ubuntu-latest` since 2026-11-05: on Namespace they waited in the
  queue behind the gates, a review run three times (5 min 48 s for 30 s of work), and GitHub's runner starts in
  seconds. They cost GitHub-hosted minutes, at least 1 minute a job. tests/test_ci.py lists each job's runner, so a new
  job chooses one on purpose. The review watchdog's `watch` is short, and the owner sets `LOOP_WATCH_RUNNER` to Namespace (the owner's call,
  2026-11-05): it runs 96 times a day, about 2,880 billed GitHub minutes a month, and a slow start does not matter to
  it. The runner is a third party that sees the code and the secret the Claude workflows use
  (`CLAUDE_CODE_OAUTH_TOKEN`).
- **The workflow block in AGENTS.md** (2026-10-16/17): pull requests only, a worktree per change started by
  `mise run start`, merge commits, no agent attribution.
