#!/bin/sh
# `mise run measure SINCE [UNTIL]`: steps 2 and 3 of .agents/skills/measure/SKILL.md. Reads the issues of every
# team of AGENTS.md "The teams" in every state with the read-only key (linear-read.sh), joins them into one file
# in a temporary folder, runs measure.py and prints the report. The folder is removed at the end. The pull-request dump
# lives in a folder of its own for the window: it stays when measure.py fails (GitHub's rate limit), so the same
# command again goes on from it, and it is removed after a report.
set -eu
here=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
root="$here/.."
[ $# -ge 1 ] || { echo "usage: mise run measure SINCE [UNTIL]   (days YYYY-MM-DD, UTC)" >&2; exit 2; }
since=$1
until_day=${2:-$(date -u +%Y-%m-%d)}
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
dumpdir="${TMPDIR:-/tmp}/measure-dump-$(id -u)-$since-$until_day"
teams=$(awk '/^## The teams$/{f=1; next} /^## /{f=0} f' "$root/AGENTS.md" | sed -n 's/^| \([A-Z][A-Z]*\) | \([A-Za-z0-9_.-]*\/[A-Za-z0-9_.-]*\) |.*/\1=\2/p')
[ -n "$teams" ] || { echo "measure: no team found in AGENTS.md \"The teams\"" >&2; exit 1; }
set --
for pair in $teams; do
  key=${pair%%=*}
  # A team with no key: linear.py fails with "no VARIABLE in the environment" (linear-read.sh passes one key only, so
  # a team of another workspace has none). It is one line, and the other teams still show.
  read=0
  "$here/linear-read.sh" list "$key" --state Triage --state Backlog --state Todo --state "In Progress" --state "In Review" \
    --state Approved --state Escalated --state Done --state Canceled > "$tmp/issues.$key.json" 2> "$tmp/err.$key" || read=$?
  cat "$tmp/err.$key" >&2
  if [ "$read" != 0 ] && grep -q '^linear\.py: no [A-Z_]* in the environment' "$tmp/err.$key"; then
    rm -f "$tmp/issues.$key.json"
    echo "measure: $key: no key, left out of the report" >&2
    continue
  fi
  [ "$read" = 0 ] || exit "$read"
  set -- "$@" --team "$pair"
done
[ $# -gt 0 ] || { echo "measure: no team could be read" >&2; exit 1; }
"${LINEAR_READ_PYTHON:-/usr/local/bin/python3}" -I - "$tmp" > "$tmp/issues.json" <<'PY'
import json, pathlib, sys
files = sorted(pathlib.Path(sys.argv[1]).glob("issues.*.json"))
print(json.dumps([issue for f in files for issue in json.loads(f.read_text())]))
PY
if [ ! -e "$dumpdir" ] && [ ! -L "$dumpdir" ]; then mkdir -m 700 "$dumpdir"; fi
if [ ! -d "$dumpdir" ] || [ -L "$dumpdir" ] || [ -z "$(find "$dumpdir" -maxdepth 0 -user "$(id -u)")" ]; then
  echo "measure: $dumpdir is not a folder of yours: remove it and run the command again." >&2
  exit 1
fi
"${LINEAR_READ_PYTHON:-/usr/local/bin/python3}" -I "$root/.agents/skills/measure/measure.py" --since "$since" --until "$until_day" \
  --issues "$tmp/issues.json" --dump "$dumpdir/prs.json" "$@" || {
  if [ -s "$dumpdir/prs.json" ] || ls "$dumpdir"/prs.*.json > /dev/null 2>&1; then
    echo "measure: stopped. The pull requests read so far stay in $dumpdir: run the same command again to go on from them: mise run measure $since $until_day" >&2
  else
    rmdir "$dumpdir"
    echo "measure: stopped before it read a pull request." >&2
  fi
  exit 1
}
rm -rf "$dumpdir"
