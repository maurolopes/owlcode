#!/bin/sh
# The job `changes` of the gates workflow: prints `needed=GATES` into GITHUB_OUTPUT, from .github/gates-needed.sh over the
# files that the pull request changes against BASE. A push to main or a run by hand (no BASE) runs every gate. This
# file is this repository's own (.workflow/loop-files.txt leaves it out).
set -eu
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
if [ -z "${BASE:-}" ]; then
  needed=python
else
  needed=$(git diff --name-only "$BASE" HEAD | sh "$here/gates-needed.sh")
fi
echo "needed=$needed" >> "$GITHUB_OUTPUT"
echo "the gates that this change needs: $needed"
