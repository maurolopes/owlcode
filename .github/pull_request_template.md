## What and why

<!-- One concern. What changed, and why. Near 500 lines where the change allows. -->

## Checks

- [ ] `mise run check` passed in the push hook (the tests run at deploy).
- [ ] A new check fails once with the code it guards broken. How: <!-- e.g. removed the fix, the check failed -->
- [ ] New browser checks: the product's list of checks updated on purpose, and its flake hunt passed.
- [ ] No check inside an `if`; a check waits for the server's record, not for an absence.
- [ ] Docs that list a command, a rule or a check are updated in the same commit.
