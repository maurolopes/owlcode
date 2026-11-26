#!/usr/bin/env bash
# The step of the job `retry` in the workflow gates-retry.yml (it was a `run:` block of the workflow: a script has tests, and
# its change needs no workflow patch). It reads the failed run's jobs and the suite's counts, asks .github/gates_retry.py
# (the rule) and reruns the failed jobs, or the whole run. It holds a write token (`actions: write`): the workflow runs
# main's copy, from a checkout of the default branch. Needs GH_TOKEN, GITHUB_REPOSITORY, RUN_ID, EVENT, HEAD and BRANCH. It
# writes jobs.json, pulls.json and suite/ in the working folder.
set -eu
gh api "repos/$GITHUB_REPOSITORY/actions/runs/$RUN_ID/jobs?per_page=100" > jobs.json
# the browser suite's counts (the file run.json of its reporter); none when the suite did not end
mkdir -p suite
gh run download "$RUN_ID" -R "$GITHUB_REPOSITORY" -n flakes-1 -D suite || echo "no counts of the browser suite"
if [ "$EVENT" = pull_request ]; then
  gh pr list -R "$GITHUB_REPOSITORY" --head "$BRANCH" --state open --json headRefOid,isCrossRepository > pulls.json
  answer=$(python3 .github/gates_retry.py jobs.json --pulls pulls.json --head "$HEAD" --suite suite/run.json)
else
  answer=$(python3 .github/gates_retry.py jobs.json --suite suite/run.json)
fi
echo "$answer"
if [ "$answer" = rerun ]; then
  gh run rerun "$RUN_ID" --failed -R "$GITHUB_REPOSITORY"
elif [ "$answer" = "rerun all" ]; then
  gh run rerun "$RUN_ID" -R "$GITHUB_REPOSITORY"
fi
