"""The edit guard (an `edit` action of guard.py, the agents' neutral entry point): a change to a file of the main checkout
is refused with the way out, `mise run start NAME` (AGENTS.md workflow block). Allowed: a file in a worktree, outside any
repository, ignored by git (local settings), or in a repository not on this workflow (no `core.hooksPath .githooks`).
An edit made through the shell is not seen here: the commit hook refuses main anyway. Standard library only.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

MESSAGE = ("This is the main checkout: a change goes in its own worktree. Run `mise run start NAME` (NAME: the issue's key, then "
           "the change in a few words, kebab case: `abc-123-short-name`), then make the change there. `mise run start NAME "
           "--carry` moves edits already made here.")


def refused(raw: str, cwd: str) -> bool:
    """True for a file of the main checkout of a repository on this workflow. A relative path is read from `cwd`."""
    if not raw:
        return False
    path = Path(raw) if Path(raw).is_absolute() else Path(cwd or ".") / raw
    folder = path.parent
    while not folder.exists() and folder != folder.parent:  # a new file in a new folder
        folder = folder.parent

    def git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["git", "-C", str(folder), *args], capture_output=True, text=True, check=False)

    top = git("rev-parse", "--show-toplevel")
    if top.returncode:
        return False  # outside any repository
    common = git("rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    if Path(top.stdout.strip()).resolve() != Path(common).resolve().parent:
        return False  # a worktree
    if git("config", "core.hooksPath").stdout.strip() != ".githooks":
        return False  # a repository not on this workflow
    return git("check-ignore", "-q", str(path)).returncode != 0  # an ignored file is local, not the code


def check(path: str, cwd: str) -> str | None:
    """The guard's answer for one edit: None allows it, a text refuses it with that text."""
    return MESSAGE if refused(path, cwd) else None
