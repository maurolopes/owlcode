---
name: triage
description: Sort the Linear Triage queue of each team (AGENTS.md "The teams") with the owner, and check what the open issues are missing, as agents/planner.md "The triage" says. Only in a session with the owner. Use when the owner says "/triage", "triage", "sort Triage", "what is the queue missing?".
---

# Sort Triage with the owner

1. Read `agents/planner.md`, section "The triage", and follow it. It is the procedure for any harness; this file only says
   when to use it.
2. Do it only when the owner is in the session. With nobody to answer, stop and say so: an autonomous run never moves an
   issue out of Triage (AGENTS.md "Autonomous runs").
3. Reach Linear with `.github/linear.py` (the key in the environment, never in a file) or with the harness's own Linear
   connection. Do the same action either way: read every issue in Triage with its relations, read every open issue, set
   the priority and the size of an issue in Triage, move an issue to Todo or to Backlog, add one comment. Never a key in a
   file or in the chat (AGENTS.md "Secrets"). A session with the Linear keys in its environment (`LINEAR_API_KEY`, and each team's variable of AGENTS.md "The teams"; the agents' macOS user, a runner) uses `.github/linear.py` for reads and writes: it sends each team's own key. In the owner's own account (not the agents' user on the same Mac), reads go through `mise run linear:read -- COMMAND ...` (`list`, `issue`: the team's read-only key from the login keychain, in that one command's environment; any other command is refused), and writes (state, labels, `triage-set`, `create`, `link`, comments) go through the harness's Linear connection, signed in with the owner's account (agents/README.md "The record").
4. Do steps 1 and 3 for each team of AGENTS.md "The teams", one team after the other, and say which team you
   sort. Start each team with the mechanical checks of step 3 as one JSON (REPO is the team's repository, whose
   open pull requests the checks read): `mise run triage:checks TEAM REPO` in the owner's own account, and
   `python3 .github/linear.py triage-checks TEAM --repo REPO` in a session with `LINEAR_API_KEY` in its environment.
   Do by hand only the checks that need judgment. A team whose key is missing (`linear.py: no VARIABLE in the environment`, which `linear:read` prints too) is one line in the report, and the other teams still show (agents/README.md "The teams").
5. You change no file of the repository, so you need no worktree.
