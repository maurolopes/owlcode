#!/bin/sh
# Reads changed file names on stdin and prints the gates that the change needs: `none` for a docs-only change
# (.github/code-changed.sh prints false), else `python`, this repository's one gate. The job `changes` of the gates
# workflow skips `python` for `none`; the review workflow reads main's copy. This file is this repository's own
# (.workflow/loop-files.txt leaves it out): a product with more gates names them in its own copy.
set -eu
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
if [ "$(sh "$here/code-changed.sh")" = false ]; then
  echo none
else
  echo python
fi
