---
name: issue
description: Turn the owner's idea, in a few words, into Linear issues in Triage (in the team of the repository the work changes, AGENTS.md "The teams") as agents/planner.md says - one issue for one pull request, or a project with several issues and their blocked-by links for a larger feature. Use when the owner says "/issue ...", "create an issue for ...", "plan this feature", "split this into tasks".
---

# Write the issues for an idea

The argument is the owner's idea, in a few words or a paragraph. If there is none, ask for it once.

1. Read `agents/planner.md` and follow it: it is the role's brief, for any harness. This file only says when to use it.
2. Choose each issue's team by the repository its pull request changes (AGENTS.md "The teams"): TEAM below.
   Work that changes two repositories is two issues, one in each team, the part that needs the other blocked by it.
3. Reach Linear with `.github/linear.py` (the key in the environment, never in a file) or with the harness's own Linear
   connection. Do the same action either way: read the open issues of every team to find the same work (`list TEAM`
   for each team), make a feature's project (`project TEAM --name NAME --body-file FILE`; when it holds issues of two
   teams, the report says so: the owner adds the second team to the project in Linear), and create each issue in Triage with its Area label,
   project, priority, size and links (`create TEAM --title TITLE --body-file FILE --label AREA --project NAME --priority
   P --size S --related KEY --blocks KEY --blocked-by KEY`). Never a key in a file or in the chat (AGENTS.md "Secrets"). `linear.py` sends each team's own key
   (AGENTS.md "The teams"). A team whose key is missing (`linear.py: no VARIABLE in the environment`, which `linear:read` prints too) is one line in the report, and the other teams still show (agents/README.md "The teams").
4. Read the code before you write, in each repository's main checkout. You change no file, so no worktree is needed.
5. When the idea is unclear in a way that changes the split (who it is for, what done looks like), ask the owner once
   in the session, with your recommendation, before you write anything. Ask every other open choice in the session
   too, before you file: the options, what each costs and your recommendation. Write each answer into the issue as
   `The owner's call, DATE: ...`. No issue leaves this skill with an open choice, and "Needs the owner" names no
   choice "before the merge" (planner.md "Before you write" step 3, "The issue"; the owner's call, 2026-11-23).
6. End with the report that planner.md asks for, as a short list, with each issue's link.
