## What and why

<!-- One concern. What changed, and why. Near 500 lines where the change allows. -->

## Checks

- [ ] `mise run check` passed in the push hook; for a code change, `mise run gates` passed too (CI runs them again).
- [ ] A new check fails once with the code it guards broken. How: <!-- e.g. removed the fix, the check failed -->
- [ ] Docs that list a command, a rule or a check are updated in the same commit.
- [ ] No product name, product team key or private repository, with the owner's patterns (`LEAK_PATTERNS_FILE=…`): `git ls-files > /tmp/f && python3 .github/leak_guard.py files /tmp/f --counts-only` (every tracked file) and
  `python3 .github/leak_guard.py stdin` (each commit message, and this text) print `total: 0`.
