#!/bin/sh
# The workflow `attribution`'s last step: read the pull request's text again from GitHub, and write it back without the
# agent attribution lines that .github/attribution.sh finds (AGENTS.md "Rules"). Needs GH_TOKEN (the implementer app's
# token: pull requests write), PR and GITHUB_REPOSITORY. The workflow runs main's copy of this file, never a branch's.
here=$(dirname "$0")
tmp=$(mktemp -d) || exit 1
trap 'rm -rf "$tmp"' EXIT
if ! gh api "repos/$GITHUB_REPOSITORY/pulls/$PR" --jq '.body // ""' > "$tmp/now.md"; then
  echo "::error::the pull request's text could not be read: remove the agent attribution lines (above) by hand"
  exit 1
fi
if sh "$here/attribution.sh" "the pull request's text" < "$tmp/now.md"; then
  echo "the text has no such line any more: nothing to remove"
  exit 0
fi
sh "$here/attribution.sh" --strip < "$tmp/now.md" > "$tmp/clean.md" || exit 1
if ! jq -Rs '{body: .}' "$tmp/clean.md" | gh api -X PATCH "repos/$GITHUB_REPOSITORY/pulls/$PR" --input - > /dev/null; then
  echo "::error::the pull request's text could not be edited: remove the agent attribution lines (above) by hand"
  exit 1
fi
echo "::notice::removed the agent attribution lines above from the pull request's text (AGENTS.md \"Rules\")"
