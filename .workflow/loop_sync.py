#!/usr/bin/python3 -I
"""The loop's files: a copy of the loop's repository at a pinned commit. This script is the loop's own (a listed file):
a product's `mise.toml` calls it from its `loop:bump` and `loop:check` tasks.

  mise run loop:bump COMMIT     move the copy to COMMIT, a commit of the loop's main
  mise run loop:check           compare the copy with the loop's repository at the pin

`.workflow/loop.toml` names the loop's repository (`repository = "OWNER/NAME"`) and one full commit hash (`commit`).
The loop's repository holds the list of its files, `.workflow/loop-files.txt` (the format: `.github/leak_guard.py`).
That repository is the source: a fix to a listed file is a pull request there, then `loop:bump`. A hand edit here makes
`loop:check` fail (`mise run gates` and the job `python` of the workflow `gates` run it).

`bump` refuses main (start a worktree first) and a commit that is not on the loop's main. It prints the commits and the
diff of the listed files from the old pin to COMMIT: the owner reads them in the bump's pull request. Then it copies each
file that the list names at COMMIT, removes each file that the loop's repository no longer holds (a file that only left
the list may be the product's own now: it stays, and the bump says so), and writes the new pin. It commits nothing. A
bump to the pin that stands changes no file, unless a listed file drifted: that file is copied back.

`check` prints each listed file that differs from the pin or is missing here, then `loop: N files differ from COMMIT`,
and exits 1 when N is not 0. A file here that no list names is not looked at: the product's own files live beside the
loop's.

The loop's repository is read through a cache outside the checkout (`LOOP_CACHE`, default `~/.cache/loop-repo.git`). The
token for a private repository comes from `LOOP_READ_TOKEN` (Contents: Read on that repository only), else from `gh auth
token`; it is sent in a header and never printed. `LOOP_REPO_URL` replaces the repository's address (the tests use it).
Standard library only; it runs on the Mac's `/usr/bin/python3` (3.9) too.
"""

from __future__ import annotations

import base64
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

PIN = Path(".workflow/loop.toml")
LIST = ".workflow/loop-files.txt"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
KEY = re.compile(r'^\s*(repository|commit)\s*=\s*"([^"]*)"\s*(?:#.*)?$')
FIX = "fix it as a pull request in the loop's repository, then `mise run loop:bump COMMIT` here"


class SyncError(Exception):
    """A reason to change nothing."""


def read_pin(root: Path) -> tuple[str, str]:
    """(repository, commit) of the pin file."""
    try:
        lines = (root / PIN).read_text().splitlines()
    except OSError as err:
        raise SyncError(f"{PIN} cannot be read ({err.strerror})") from None
    found = {m.group(1): m.group(2) for line in lines if (m := KEY.match(line))}
    if not found.get("repository") or not FULL_SHA.match(found.get("commit", "")):
        raise SyncError(f'{PIN} needs `repository = "OWNER/NAME"` and `commit = "<40 hex digits>"`')
    return found["repository"], found["commit"]


def write_pin(root: Path, new: str) -> None:
    lines = (root / PIN).read_text().splitlines(keepends=True)
    out = [re.sub(r'"[^"]*"', f'"{new}"', line, count=1) if (m := KEY.match(line.rstrip("\n"))) and m.group(1) == "commit"
           else line for line in lines]
    (root / PIN).write_text("".join(out))


def token() -> str:
    """The read token, or "" (a public repository needs none)."""
    found = os.environ.get("LOOP_READ_TOKEN", "")
    if found:
        return found
    try:
        run = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=False)
    except OSError:
        return ""
    return run.stdout.strip() if run.returncode == 0 else ""


class Loop:
    """The loop's repository, read through a bare cache."""

    def __init__(self, repository: str) -> None:
        self.url = os.environ.get("LOOP_REPO_URL") or f"https://github.com/{repository}.git"
        self.cache = Path(os.environ.get("LOOP_CACHE") or Path.home() / ".cache" / "loop-repo.git")

    def git(self, *args: str, auth: bool = False, check: bool = True) -> subprocess.CompletedProcess[str]:
        cmd = ["git", "--git-dir", str(self.cache)]
        if auth and (secret := token()):
            basic = base64.b64encode(f"x-access-token:{secret}".encode()).decode()
            cmd += ["-c", f"http.extraHeader=Authorization: Basic {basic}"]
        run = subprocess.run([*cmd, *args], capture_output=True, text=True, check=False)
        if check and run.returncode != 0:
            last = run.stderr.strip().splitlines()[-1:] or ["no message"]
            raise SyncError(f"git {args[0]} failed on the loop's repository" + (
                " (is LOOP_READ_TOKEN set, with Contents: Read on it?)" if auth else f": {last[0]}"))
        return run

    def fetch(self) -> None:
        if not self.cache.exists():
            self.cache.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "init", "-q", "--bare", str(self.cache)], check=True, capture_output=True)
        self.git("fetch", "-q", "--no-tags", self.url, "+refs/heads/main:refs/heads/main", auth=True)

    def has(self, commit: str) -> bool:
        return self.git("cat-file", "-e", f"{commit}^{{commit}}", check=False).returncode == 0

    def resolve(self, wanted: str) -> str:
        """The full hash of `wanted`, which the loop's main must hold."""
        self.fetch()
        run = self.git("rev-parse", "--verify", f"{wanted}^{{commit}}", check=False)
        sha = run.stdout.strip()
        if run.returncode != 0 or self.git("merge-base", "--is-ancestor", sha, "main", check=False).returncode != 0:
            raise SyncError(f"{wanted} is not on the loop's main: a pin names a merged commit")
        return sha

    def ensure(self, commit: str) -> None:
        if not self.has(commit):
            self.fetch()
        if not self.has(commit):
            raise SyncError(f"the pin {commit} is not in the loop's repository")

    def extract(self, commit: str, into: Path) -> list[str]:
        """The files that the list at `commit` names, written into `into`; their paths."""
        self.ensure(commit)
        tree = into / "tree"
        tree.mkdir(parents=True)
        archive = into / "tree.tar"
        with archive.open("wb") as out:
            if subprocess.run(["git", "--git-dir", str(self.cache), "archive", commit], stdout=out, check=False).returncode:
                raise SyncError(f"git archive {commit} failed")
        with tarfile.open(archive) as tar:
            tar.extractall(tree)
        if not (tree / LIST).is_file():
            raise SyncError(f"the loop's repository has no {LIST} at {commit}")
        names, _stale = leak_guard().listed(tree / LIST, tree)
        return names


def leak_guard():  # type: ignore[no-untyped-def]
    """The list's reader: one glob rule for the loop's files, as `.github/leak_guard.py` has it."""
    spec = importlib.util.spec_from_file_location("leak_guard", Path(__file__).resolve().parent.parent / ".github" / "leak_guard.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def same(a: Path, b: Path) -> bool:
    if not b.is_file():
        return False
    return a.read_bytes() == b.read_bytes() and (a.stat().st_mode & 0o111 != 0) == (b.stat().st_mode & 0o111 != 0)


def check(root: Path) -> int:
    repository, commit = read_pin(root)
    loop = Loop(repository)
    with tempfile.TemporaryDirectory() as tmp:
        names = loop.extract(commit, Path(tmp))
        bad = [n for n in names if not same(Path(tmp) / "tree" / n, root / n)]
    for name in bad:
        print(f"{name}: " + ("missing" if not (root / name).is_file() else f"differs from {commit[:12]}"))
    print(f"loop: {len(bad)} files differ from {commit[:12]}")
    if bad:
        print(f"loop:check: the loop's files come from the loop's repository; {FIX}", file=sys.stderr)
    return 1 if bad else 0


def bump(root: Path, wanted: str) -> int:
    run = subprocess.run(["git", "branch", "--show-current"], cwd=root, capture_output=True, text=True, check=False)
    if run.stdout.strip() in ("", "main"):
        raise SyncError("run it on a branch of a worktree: mise run start abc-123-bump-the-loop")
    repository, old = read_pin(root)
    loop = Loop(repository)
    new = loop.resolve(wanted)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_old, tmp_new = Path(tmp) / "old", Path(tmp) / "new"
        tmp_old.mkdir()
        tmp_new.mkdir()
        old_names = loop.extract(old, tmp_old)
        new_names = loop.extract(new, tmp_new)
        if new != old:
            if loop.git("merge-base", "--is-ancestor", old, new, check=False).returncode != 0:
                print(f"note: {new[:12]} does not follow the old pin {old[:12]} on main: the pin moves back or sideways")
            print(f"the loop {old[:12]}..{new[:12]}, the commits:")
            print(loop.git("log", "--oneline", f"{old}..{new}").stdout, end="")
            print("\nthe change of the listed files (read it):")
            paths = sorted(set(old_names) | set(new_names))
            print(loop.git("diff", old, new, "--", *paths).stdout or "(none)\n", end="")
        # A file that left the list may be the product's own now (its CODEOWNERS): it is removed only when the loop's
        # repository no longer holds it at all.
        removed = [n for n in old_names if n not in set(new_names) and not (Path(tmp) / "new" / "tree" / n).exists()]
        for name in old_names:
            if name not in set(new_names) and name not in removed:
                print(f"kept {name}: it left the list, and the loop's repository still holds a file there")
        changed = len(removed)
        for name in removed:
            (root / name).unlink(missing_ok=True)
            print(f"removed {name}")
        for name in new_names:
            source, target = Path(tmp) / "new" / "tree" / name, root / name
            if not same(source, target):
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                print(f"copied {name}")
                changed += 1
    write_pin(root, new)
    print(f"{PIN}: {new}" if new != old else f"the pin stands at {new}: {changed} files changed")
    return 0


def main(argv: list[str]) -> int:
    try:
        if argv[:1] == ["check"] and len(argv) == 1:
            return check(Path("."))
        if argv[:1] == ["bump"] and len(argv) == 2 and not argv[1].startswith("-"):
            return bump(Path("."), argv[1])
    except SyncError as err:
        print(f"loop: {err}", file=sys.stderr)
        return 1
    print(__doc__.split("\n\n")[1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
