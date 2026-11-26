#!/usr/bin/env bash
# The step of the job `file` in the workflow flakes.yml (it was a `run:` block of the workflow: a script has tests, and its
# change needs no workflow patch). It downloads the file of flaky tests of a gates run and gives it to .github/flakes.py,
# which files or updates the issues. It holds the Linear key: the workflow runs main's copy, from a checkout of the default
# branch. Needs GH_TOKEN, LINEAR_API_KEY, GITHUB_REPOSITORY, RUN_ID, ATTEMPT, RUN, HEAD and BRANCH. LINEAR_TEAM, when set,
# is the Linear team of the issues; else flakes.py takes the team whose row in AGENTS.md "The teams" names
# GITHUB_REPOSITORY, and stops with a message when there is none. It writes flakes/ in the working folder.
# A run of a fork's pull request files nothing: its file of flaky tests is the fork's text, and the issue it would file
# goes to Todo with the fast lane, where an agent starts it (agents/README.md "Fork pull requests"). The run's head
# repository comes from GitHub; a failed read stops the step, so an unknown run files nothing.
set -eu
head_repo=$(gh api "repos/$GITHUB_REPOSITORY/actions/runs/$RUN_ID" --jq '.head_repository.full_name // ""')
if [ "$head_repo" != "$GITHUB_REPOSITORY" ]; then
  echo "::notice::the run $RUN_ID is of the repository '${head_repo:-unknown}', not of $GITHUB_REPOSITORY (a fork): no flaky test is filed"
  exit 0
fi
mkdir -p flakes
if ! gh run download "$RUN_ID" -R "$GITHUB_REPOSITORY" -n "flakes-$ATTEMPT" -D flakes; then
  echo "no file of flaky tests: the web job did not run the suite"
  exit 0
fi
python3 -I .github/flakes.py flakes/flakes.json --team "${LINEAR_TEAM:-}" --repo "$GITHUB_REPOSITORY" --run "$RUN" --head "$HEAD" --branch "$BRANCH"
