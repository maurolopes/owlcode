#!/usr/bin/env python3
"""Start a change: `mise run start NAME [--from BRANCH] [--carry [PATH ...]]` (AGENTS.md workflow block). The agent runs it before its first
change to a tracked file; nobody runs it by hand. In order:

1. fetch, and update the main checkout (`git pull --ff-only`: it never merges);
2. remove every worktree under .claude/worktrees whose pull request is merged, whose tree is clean and whose branch has no
   commit beyond origin/main (finished work, whoever made it), and its local branch; list the others with the reason kept;
3. refuse a NAME with no issue key (a team key of 2 to 5 letters, a dash, a number: `abc-123-short-name`), or `try-...` for a measurement, or `loop-bump-SHORT` for a pure bump of the loop's files, then make
   .claude/worktrees/NAME on a new branch NAME from origin/main, or from origin/BRANCH with --from BRANCH (a draft
   stack on its blocker's branch: agents/README.md "What can start now"); when the branch NAME exists already (a
   restarted run, or one after the owner's answer, works on its pull request's branch: agents/implementer.md), on that
   branch, brought up to origin/NAME, and --from changes nothing;
4. with --carry, move the main checkout's uncommitted changes there (every tracked change, and the untracked PATHs named):
   a question that turned into an edit on main loses nothing; the main checkout is restored only after the move applied;
5. install the dependencies there (`mise run start:deps`, the product's own task), then warm the web lint's caches in the
   background (`mise run start:warm`: a cold ESLint takes about 50 s, a warm one 1 s, and the caches belong to the folder).

The last line printed is the new worktree's path. Standard library only; git and gh do the work.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

NAME = re.compile(r"[a-z0-9][a-z0-9._-]{0,62}")
# A change has its issue first (AGENTS.md "Rules"): its key starts the branch's name, so Linear links the pull request and the
# review finds the issue. A measurement's throwaway branch starts with `try-` and gets no pull request (DEVELOPMENT.md
# "Measuring a CI change"). A pure bump of the loop's files is `loop-bump-SHORT` (SHORT: 12 hex digits) and needs no issue
# (`mise run loop:bump`, .workflow/loop_sync.py: the push hook and the review's start check that its diff is a pure bump).
KEYED = re.compile(r"[a-z]{2,5}-\d+(-[a-z0-9._-]+)?|try-[a-z0-9._-]+|loop-bump-[0-9a-f]{12}")
UNKEYED = ("{name!r} has no issue key: a change starts from its issue. File it first (the skill `issue`, or "
           "`python3 .github/linear.py create TEAM ...`), then run `mise run start abc-123-short-name` with its key. A "
           "measurement's throwaway branch is named `try-...` and gets no pull request. A bump of the loop's files is `mise run "
           "loop:bump`, with no issue.")
WORKTREES = Path(".claude") / "worktrees"


class Refused(Exception):
    pass


def git(cwd: Path, *args: str, stdin: str | None = None) -> str:
    done = subprocess.run(["git", "-C", str(cwd), *args], input=stdin, capture_output=True, text=True, check=False)
    if done.returncode:
        raise Refused(f"git {' '.join(args)}: {(done.stderr or done.stdout).strip()}")
    return done.stdout.strip()


def main_checkout(here: Path) -> Path:
    return Path(git(here, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve().parent


def worktrees(main: Path) -> list[tuple[Path, str | None]]:
    """(path, branch) of each worktree under .claude/worktrees; branch None when detached."""
    out: list[tuple[Path, str | None]] = []
    for block in git(main, "worktree", "list", "--porcelain").split("\n\n"):
        fields = dict(line.split(" ", 1) for line in block.splitlines() if " " in line)
        path = Path(fields.get("worktree", "")).resolve()
        if path.parent == main / WORKTREES:
            branch = fields.get("branch", "")
            out.append((path, branch.removeprefix("refs/heads/") or None))
    return out


def pull_requests(main: Path) -> dict[str, str] | None:
    """The newest pull request's state (OPEN, MERGED, CLOSED) per head branch; None when gh can't say."""
    try:
        done = subprocess.run(["gh", "pr", "list", "--state", "all", "--limit", "500", "--json", "headRefName,state"],
                              cwd=main, capture_output=True, text=True, check=False, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode:
        return None
    states: dict[str, str] = {}
    for pr in json.loads(done.stdout or "[]"):  # newest first: the first one per branch wins
        states.setdefault(pr["headRefName"], pr["state"])
    return states


def sweep(main: Path, say: list[str]) -> None:
    """Remove the finished worktrees; say why each other one stays."""
    states = pull_requests(main)
    if states is None:
        say.append("worktrees: not swept (gh could not list the pull requests)")
        return
    for path, branch in worktrees(main):
        state = states.get(branch or "")
        if branch is None:
            why = "detached"
        elif state != "MERGED":
            why = {"OPEN": "pull request open", "CLOSED": "pull request closed, not merged"}.get(state or "", "no pull request")
        elif git(path, "status", "--porcelain"):
            why = "merged, but files not committed"
        elif int(git(main, "rev-list", "--count", f"origin/main..{branch}")):
            why = "merged, but commits not in main"
        else:
            try:  # never --force: a locked worktree stays
                git(main, "worktree", "remove", str(path))
                git(main, "branch", "-D", branch)
            except Refused as e:
                why = f"not removed ({e})"
            else:
                say.append(f"removed {path.name} (merged)")
                continue
        say.append(f"kept {path.name}: {why}")


def carry(main: Path, new: Path, paths: list[str]) -> list[str]:
    """Move the main checkout's tracked changes and the named untracked files into `new`; the names moved."""
    untracked = set(git(main, "ls-files", "--others", "--exclude-standard").splitlines())
    for p in paths:
        if p not in untracked:
            raise Refused(f"--carry {p}: not an untracked file of the main checkout (tracked changes move without naming)")
    patch = git(main, "diff", "HEAD", "--binary", "--full-index") + "\n"
    changed = git(main, "diff", "HEAD", "--name-only").splitlines()
    if changed:
        git(new, "apply", "--3way", "--whitespace=nowarn", "-", stdin=patch)
    for p in paths:
        (new / p).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(main / p, new / p)
    # only now, with everything in the new worktree: the main checkout back to its last commit
    if changed:
        git(main, "restore", "--source=HEAD", "--staged", "--worktree", "--", *changed)
    for p in paths:
        (main / p).unlink()
    return changed + paths


def add_worktree(main: Path, new: Path, name: str, base: str = "main") -> str:
    """Make the worktree `new` on the branch `name`: a new branch from origin/`base` (main, or a blocker's branch for a
    draft stack), or the branch that exists already (the local one, else the remote one), never a new branch beside it. A
    local branch that has diverged from origin is refused: which commits count is not start's call."""
    local = bool(git(main, "branch", "--list", name))
    remote = bool(git(main, "branch", "--remotes", "--list", f"origin/{name}"))
    if not local and not remote:
        if not git(main, "branch", "--remotes", "--list", f"origin/{base}"):
            raise Refused(f"--from {base}: origin has no such branch (the blocker's branch is pushed first)")
        # a stacked branch never tracks its blocker's branch: a plain `git push` must not reach the blocker's pull request
        track = ["--no-track"] if base != "main" else []
        git(main, "worktree", "add", "-q", *track, str(new), "-b", name, f"origin/{base}")
        return f"branch {name}: new, from origin/{base}"
    if not local:
        git(main, "worktree", "add", "-q", "--track", "-b", name, str(new), f"origin/{name}")
        return f"branch {name}: exists, from origin/{name}"
    git(main, "worktree", "add", "-q", str(new), name)
    if remote:
        try:
            git(new, "merge", "--ff-only", "-q", f"origin/{name}")
        except Refused:
            git(main, "worktree", "remove", "--force", str(new))  # made a moment ago: nothing in it but the branch's commits
            raise Refused(f"branch {name} and origin/{name} have diverged: bring them together first") from None
    return f"branch {name}: exists" + (f", up to origin/{name}" if remote else ", local only")


def start(name: str, carried: list[str] | None, here: Path, base: str = "main") -> list[str]:
    for value in (name, base):
        if not NAME.fullmatch(value):
            raise Refused(f"{value!r}: a name is lower case letters, digits, dots, dashes (the branch and the folder)")
    if not KEYED.fullmatch(name):
        raise Refused(UNKEYED.format(name=name))
    main = main_checkout(here)
    new = main / WORKTREES / name
    if new.exists():
        raise Refused(f"{new} exists: work there, or pick another name")
    say: list[str] = []
    git(main, "fetch", "--prune", "-q", "origin")
    if git(main, "symbolic-ref", "--short", "-q", "HEAD") == "main" and carried is None:
        try:
            git(main, "pull", "--ff-only", "-q")
            say.append("main checkout: up to date")
        except Refused as e:
            say.append(f"main checkout: not updated ({e})")
    sweep(main, say)
    line = add_worktree(main, new, name, base)
    say.append(line)
    if carried is not None:
        try:
            moved = carry(main, new, carried)
        except Refused:
            git(main, "worktree", "remove", "--force", str(new))  # made a moment ago, holds only the failed move
            if line.startswith(f"branch {name}: new,"):  # a branch that existed before keeps its commits
                git(main, "branch", "-D", name)
            raise
        say.append(f"carried: {', '.join(moved) or 'nothing (no changes)'}")
        try:  # now clean: the main checkout can catch up
            git(main, "pull", "--ff-only", "-q")
        except Refused as e:
            say.append(f"main checkout: not updated ({e})")
    deps = subprocess.run(["sh", "-c", "mise trust -q >/dev/null 2>&1; mise run start:deps"], cwd=new, check=False)
    say.append("dependencies: installed" if deps.returncode == 0 else "dependencies: NOT installed (mise run start:deps failed)")
    if deps.returncode == 0:  # only a warm-up: nobody waits for it and nothing reads its result
        subprocess.Popen(["sh", "-c", "mise run start:warm"], cwd=new, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        say.append("web lint: warming its caches in the background")
    say.append(str(new))
    return say


def main() -> int:
    args = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    args.add_argument("name")
    args.add_argument("--from", dest="base", default="main", metavar="BRANCH",
                      help="a new branch starts from origin/BRANCH, not origin/main (a draft stack on its blocker's branch)")
    args.add_argument("--carry", nargs="*", metavar="PATH", help="move the main checkout's uncommitted changes (and these untracked files)")
    a = args.parse_args()
    try:
        lines = start(a.name, a.carry, Path.cwd(), a.base)
    except Refused as e:
        print(f"start: {e}", file=sys.stderr)
        return 1
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
