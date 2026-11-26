#!/bin/sh
# Reads a commit message or a pull request's text on stdin. It prints each agent attribution line and exits 1, or prints
# nothing and exits 0 (exit 2: grep failed). AGENTS.md "Rules": no agent attribution in a commit message or a pull request
# text, also when the harness asks for it (commit 60c9e01 carried a session line all the same).
# The pattern list of the commit-msg hook (.githooks/commit-msg), the gates workflow's job `changes` and the workflow
# `attribution`, which runs main's copy of this file.
# $1, when given, is the name of the text, put in front of each line found ("commit 1a2b3c4").
# With `--strip` as $1, it prints the text without those lines and exits 0 (exit 2: grep failed): the workflow
# `attribution` cleans a pull request's text with it.
# A pull request's text is in no commit, so a job can clean it; a commit cannot change, so the hook and the gates refuse it.
# Only the shapes a harness writes, never a word in prose: a trailer at the start of a line (a session line; a co-author
# line with an agent's address), a session link with its scheme and its id, and the generated footer with its Markdown
# link. A co-author line is an agent's by its address, never by its name: a person can be called Claude or Aider.
# A sentence that says "co-authored by Claude", or names the trailer, or quotes a link without the scheme, passes
# (tests/test_attribution.py). -a: a byte that is not valid in the locale makes GNU grep call the text binary otherwise.
# GitHub sends a pull request's text with CRLF line ends: the CR goes first.
attribution() {
  tr -d '\r' | grep -a -i -E "$@" \
    -e '^[[:space:]]*claude-session:' \
    -e '^[[:space:]]*co-authored-by:.*<[^>]*(@anthropic\.com|codex@openai\.com|noreply@openai\.com|cursoragent@cursor\.com|@aider\.chat|copilot@users\.noreply\.github\.com|\[bot\]@users\.noreply\.github\.com)>' \
    -e 'https?://claude\.ai/code/session_[[:alnum:]]' \
    -e '^[^[:alnum:]]*generated (with|by) \[(claude|codex|copilot|cursor|gemini)'
}
if [ "${1:-}" = --strip ]; then
  attribution -v
  case $? in
    0 | 1) exit 0 ;;  # 1: no line is left, every line was an attribution line
    *) exit 2 ;;
  esac
fi
found=$(attribution -n)
case $? in
  0) ;;
  1) exit 0 ;;
  *) exit 2 ;;
esac
printf '%s\n' "$found" | while IFS= read -r line; do
  printf '%sline %s: %s\n' "${1:+$1, }" "${line%%:*}" "${line#*:}"
done
exit 1
