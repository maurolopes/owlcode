---
name: measure
description: Print the loop's numbers (agents/README.md "What to measure") for a window of days, from GitHub and the Linear issues, with the same definitions each time, and a table by model and effort level from the run lines, then the same table by the local review's model. Use when the owner says "/measure", "/measure --since 2026-11-02", "measure the loop", "how is the loop doing?".
---

# Measure the loop

The numbers and their meaning are in agents/README.md "What to measure". The script `measure.py` beside this file
computes them, each with its definition in a comment, so two measurements with the same window give the same numbers.
It reads and prints: it changes nothing on GitHub or in Linear, and it needs no worktree.

1. Take the window from the owner's words: `--since DATE` (the first day) and `--until DATE` (the day after the last;
   default today), days in UTC. With no window, use the past seven days.
2. In the owner's own account (not the agents' macOS user on the same Mac), steps 2 and 3 are one command: `mise run measure SINCE [UNTIL]` (the days of step 1). It reads
   the issues of every team with its read-only key from the keychain, runs `measure.py` and prints the report; a team
   with no key there is one line (`measure: TEAM: no key, left out of the report`), and the other teams still show. Go on
   at step 4.
   For a run with the key in its environment, do steps 2 and 3 as they stand:
   Read the issues of each team of AGENTS.md "The teams" with their labels, states and comments, in every state,
   into one file in the session's temporary folder (the teams' lists joined into one JSON list): `python3
   .github/linear.py list TEAM --state Triage --state Backlog --state Todo --state "In
   Progress" --state "In Review" --state Approved --state Escalated --state Done --state Canceled` (the key in the
   environment, never in a file), or the same issues with the harness's own Linear connection, written in that
   command's shape (identifier, state, labels, created, comments with created and body). Never a key in a file or in
   the chat (AGENTS.md "Secrets"). A team whose key is missing (`linear.py: no VARIABLE in the environment`, which `linear:read` prints too) is one line in the report, and the other teams still show (agents/README.md "The teams"). Leave that team's
   `--team` out of step 3.
3. Run `python3 .agents/skills/measure/measure.py --since DATE --until DATE --issues FILE --dump PRS`, with one `--team
   TEAM=REPO` for each row of "The teams", where PRS is a file in the same temporary folder. It reads every merged pull
   request of the window with `gh` (one request each: a few minutes for 250), one file per team beside PRS. To print the
   report again, give `--from-dump PRS` instead of `--dump PRS`.
4. Show the owner the tables as the script prints them: each team's tables, then the total of all teams. The second
   table groups the issues by their run's model and effort level, and the third, when a line names it, by the local
   review's: a review on another model than the run's finds other faults, so the two groups read apart.
   Under them, put the baseline of agents/README.md "What to measure" beside each number that it also has, and name
   the window of each.

The script interprets nothing. A change to the loop's rules is the owner's call, and the numbers are its evidence.
