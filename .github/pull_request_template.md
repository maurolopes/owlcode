## What and why

<!-- One concern. What changed, and why. Near 500 lines where the change allows. -->

## Checks

- [ ] `mise run check` passed in the push hook; for a code change, `mise run gates` passed too (CI runs them again).
- [ ] A new check fails once with the code it guards broken. How: <!-- e.g. removed the fix, the check failed -->
- [ ] Docs that list a command, a rule or a check are updated in the same commit.
- [ ] No product name, product team key or private repository: the check `leak` runs the owner's patterns on every
  tracked file, each commit message, and this title and text (a run does not look for the patterns file). The owner can
  also run it locally, from the owner's account, with `LEAK_PATTERNS_FILE=…`: `git ls-files > /tmp/f && python3
  .github/leak_guard.py files /tmp/f --counts-only` and `python3 .github/leak_guard.py stdin` print `total: 0`.
