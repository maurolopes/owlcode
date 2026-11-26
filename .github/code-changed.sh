#!/bin/sh
# Reads changed file names on stdin and prints "true" when any is code, "false" when all are docs. The review workflow
# reads main's copy (the fact CODE, the docs-only rules of AGENTS.md "Autonomous runs") and the pre-push hook runs it: a
# docs-only push runs no check. This file is this repository's own: each repository names its own docs
# (.workflow/loop-files.txt leaves it out). The docs: the Markdown files at the root and the workflow's prose in agents/.
# `git diff --name-only` puts a name with a non-ASCII byte in quotes ("caf\303\251.md", core.quotePath): the quotes go
# first, else a docs file reads as code. The escapes stay: they change no folder and no extension.
while IFS= read -r f || [ -n "$f" ]; do
  case $f in
    \"*\") f=${f#\"}; f=${f%\"} ;;
  esac
  case $f in
    agents/*.md) ;;
    */*) echo true; exit 0 ;;
    *.md) ;;
    *) echo true; exit 0 ;;
  esac
done
echo false
