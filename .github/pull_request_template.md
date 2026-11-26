## What and why

<!-- One concern. What changed, and why. Near 500 lines where the change allows. -->

## Checks

- [ ] `mise run check` passed in the push hook; for a code change, `mise run gates` passed too (CI runs them again).
- [ ] A new check fails once with the code it guards broken. How: <!-- e.g. removed the fix, the check failed -->
- [ ] Docs that list a command, a rule or a check are updated in the same commit.
- [ ] No product name, product team key or private repository in any file, commit or this text: `LEAK_PATTERNS_FILE=… mise run leak:count` prints `total: 0`.
