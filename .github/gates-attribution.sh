#!/bin/sh
# The job `changes` of the gates workflow runs this on a pull request: it reads the message of each commit in BASE..HEAD
# (merge commits left out: git writes them) through .github/attribution.sh, the pattern list of the commit-msg hook, and
# fails when one has an agent attribution line (AGENTS.md "Rules"; agents/README.md "No agent attribution line"). A
# pushed commit cannot change, so the job `changes` stays red. On a code change the skipped job `python` then fails the
# check `verdict`; on a docs-only change `python` is skipped and passes, so only the red job shows the line. With no BASE (a push to main, a run by
# hand) it checks nothing. This file is this repository's own (.workflow/loop-files.txt leaves it out).
set -u
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
[ -n "${BASE:-}" ] || exit 0
commits=$(git rev-list --no-merges "$BASE..HEAD") || exit 2
failed=0
for commit in $commits; do
  found=$(git log -1 --format=%B "$commit" | sh "$here/attribution.sh" "commit $(echo "$commit" | cut -c1-7)")
  case $? in
    0) ;;
    1) printf '%s\n' "$found"; failed=1 ;;
    *) echo "gates-attribution: the check failed on commit $commit"; exit 2 ;;
  esac
done
[ "$failed" -eq 0 ] || echo "AGENTS.md \"Rules\" refuses agent attribution in a commit message: the pull request needs the owner's decision"
exit "$failed"
