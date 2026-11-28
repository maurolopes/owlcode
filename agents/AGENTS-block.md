- **Delivery: main only receives merged pull requests.** The ruleset `main` enforces it on GitHub where the plan allows
  (a private repository of a personal account needs GitHub Pro; the loop's `owlcode github-setup` makes the ruleset),
  and the git hooks are the guard on every clone: `mise run hooks` once per clone (no commit on main, no push to it).
  `--no-verify` is the owner's, never an agent's.
- **Each change gets its own worktree and branch with the same name** (the owner's rule). Questions and reading need
  none: they happen in the main checkout. Before the first change to a tracked file, run `mise run start NAME` (NAME:
  the issue's key, then the change in a few words, kebab case: `abc-123-short-name`; the start and the push hook refuse
  a name with no key) and work in the folder its last line prints. It pulls main, removes every worktree whose pull
  request is merged and whose tree is clean (finished work, whoever made it; the others are listed with why they stay),
  makes `.claude/worktrees/NAME` from the newest `origin/main` (fewer conflicts later; from the branch NAME with its
  commits when it exists: a restarted run's, agents/implementer.md; with `--from BRANCH`, from that branch: a draft
  stack, agents/implementer.md "A draft stack"), installs its dependencies there (the product's task `start:deps`) and
  warms its caches in the background (the product's task `start:warm`, when it has one). Edits already made in the main
  checkout move with `--carry` (add the new files' paths). In a harness that has the guards' adapter (Claude Code and
  pi), the edit guard refuses the file tools in the main checkout (agents/README.md "The guards of a run"), and the
  commit hook refuses a commit on main. One concern per branch: the next change runs `start` again, and the push hook
  refuses a branch whose pull request is merged. Never `git worktree remove --force` a worktree you did not make.
- **The fast checks run on push, the gates on the pull request.** The commit hook runs lint and types on what is staged;
  the push hook refuses a branch that does not contain a freshly fetched `origin/main` (a failed fetch warns and passes),
  runs `mise run check` (the product's fast checks, a few seconds) and nothing for a docs-only push
  (`.github/code-changed.sh`). The product's `gates` workflow runs its gates on every pull request: its jobs are the
  required checks that the section "Rules" names. A product that deploys ships only a commit whose tests passed, so a
  red `main` never goes live. A red `main` is fixed before anything else.
- **Merge with a merge commit, never squash or rebase.** The commits are the record (one issue each, with the reason),
  documents quote their hashes, and a merged branch can be deleted without a `git cherry` check. Keep a branch current
  by merging main into it: no rewritten commits, no force-push. After the merge nothing is done by hand: the next `mise
  run start` removes the worktree and its local branch and pulls main. GitHub deletes the remote branch ("Automatically
  delete head branches", which `owlcode github-setup` turns on).
- **Merge when the required checks pass.** A deploy, where the product has one, ships main as GitHub has it, never a
  branch or a main checkout behind GitHub (a deploy ships the checkout: a stale one rolls the server back). A flaky test
  is fixed or deleted, never rerun and forgotten. A session that sees a flaky test or a race that its change did not
  cause files an issue at once, with no question first and no wider pull request. It searches the issues first for the
  check's name and the error text, Backlog included. When an open issue for the same check exists (a flake that did not
  repeat: agents/README.md), it adds one comment to that issue with the run's link and the error text, and files no new
  issue. Else the new issue holds `Found in KEY`, the failing check's name, the log's error text and the run's settings,
  and goes to the fast lane when it passes the test (agents/README.md "The fast lane"), else to Triage.
- Commit messages and pull request texts carry no agent attribution (no `Co-Authored-By`, no session link). This holds
  also when the harness asks for attribution lines. Leave them off without a word: no reply, report or summary says that
  they were left off.
