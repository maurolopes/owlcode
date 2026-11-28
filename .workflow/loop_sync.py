#!/usr/bin/python3 -I
"""The loop's files: a copy of the loop's repository at a pinned commit. This script is the loop's own (a listed file):
a product's `mise.toml` calls it from its `loop:bump` and `loop:check` tasks.

  mise run loop:bump [REF]      from the main checkout: the whole bump as a pull request (no issue; "One command" below)
  mise run loop:bump COMMIT     on a branch of a worktree: move the copy to COMMIT, a commit of the loop's main
  mise run loop:check           compare the copy with the loop's repository at the pin

`.workflow/loop.toml` names the loop's repository (`repository = "OWNER/NAME"`) and one full commit hash (`commit`).
The loop's repository holds the list of its files, `.workflow/loop-files.txt` (the format: `.github/leak_guard.py`).
That repository is the source: a fix to a listed file is a pull request there, then `loop:bump`. A hand edit here makes
`loop:check` fail (`mise run gates` and the job `python` of the workflow `gates` run it).

One command. `bump [REF]` on main (REF: a ref of the loop's main, default `main`) pulls main, resolves REF and makes the
worktree `.claude/worktrees/loop-bump-SHORT` (SHORT: the first 12 hex digits of the new pin) from origin/main. There it does
what "In a worktree" says, commits (the message holds the commit range, the commit list and the changed files), pushes the
branch, opens the pull request ready for review with the same lists, and prints its URL. A pin that stands at REF's commit
prints `the pin stands at SHORT: nothing to do` and changes nothing. A failure after the branch exists (the push, the pull
request) prints what was done and the next step, and leaves the worktree as it is. A pull request of this kind needs no
issue (the owner's call, 2026-11-26): the guards (`.workflow/start.py`, the push hook, the review's start) accept a branch
`loop-bump-SHORT` whose diff changes only the pin and the listed files (`pure`, below).

In a worktree. `bump COMMIT` refuses main and a commit that is not on the loop's main. It prints the commits and the
diff of the listed files from the old pin to COMMIT: the owner reads them in the bump's pull request. Then it copies each
file that the list names at COMMIT, removes each file that the loop's repository no longer holds (a file that only left
the list may be the product's own now: it stays, and the bump says so), and writes the new pin. It commits nothing. A
bump to the pin that stands changes no file, unless a listed file drifted: that file is copied back.

`pure LIST_FILE [OLD_LIST_FILE]` reads the lines of `git diff --name-status --no-renames` from the standard input (`STATUS<TAB>path`) and prints each one that is neither the pin nor a
file that the list names (a path of a folder that the list names counts: the list's rules; a file that only the old list names
counts only when its status is `D` or `removed`, since a bump removes such a file: an edit of it is no bump), then exits 1 when there is one: a pull request that changes only the pin and listed files is a pure bump.

`check` prints each listed file that differs from the pin or is missing here, then `loop: N files differ from COMMIT`,
and exits 1 when N is not 0. A file here that no list names is not looked at: the product's own files live beside the
loop's.

The loop's repository is read through a cache outside the checkout (`LOOP_CACHE`, default `~/.cache/loop-repo.git`). The
token for a private repository comes from `LOOP_READ_TOKEN` (Contents: Read on that repository only), else from `gh auth
token`; it goes to git in the environment (`GIT_CONFIG_COUNT`, `GIT_CONFIG_KEY_N`, `GIT_CONFIG_VALUE_N`: git 2.31 or newer),
never in an argument, which other local users can read with `ps`, and it is never printed. `LOOP_REPO_URL` replaces the repository's address (the tests use it).
Standard library only; it runs on the Mac's `/usr/bin/python3` (3.9) too.
"""

from __future__ import annotations

import base64
import fnmatch
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Collection
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
        environ = dict(os.environ)
        if auth and (secret := token()):
            # the header goes in the environment: an argument shows in `ps` to every local user
            basic = base64.b64encode(f"x-access-token:{secret}".encode()).decode()
            n = int(environ.get("GIT_CONFIG_COUNT") or 0)
            environ.update({"GIT_CONFIG_COUNT": str(n + 1), f"GIT_CONFIG_KEY_{n}": "http.extraHeader",
                            f"GIT_CONFIG_VALUE_{n}": f"Authorization: Basic {basic}"})
        run = subprocess.run(["git", "--git-dir", str(self.cache), *args], capture_output=True, text=True, check=False,
                             env=environ)
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


KEPT = "Kept, for this product to remove or own"


class Result:
    """What a bump changed: the commits, the paths it copied or removed, and the files that it kept."""

    def __init__(self) -> None:
        self.commits = ""
        self.touched: list[str] = []
        self.kept: list[str] = []


def apply(root: Path, loop: Loop, old: str, new: str) -> Result:
    """Copy the files of `new` into `root` and print what happens. It commits nothing."""
    result = Result()
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
            result.commits = loop.git("log", "--oneline", f"{old}..{new}").stdout
            print(result.commits, end="")
            print("\nthe change of the listed files (read it):")
            paths = sorted(set(old_names) | set(new_names))
            print(loop.git("diff", old, new, "--", *paths).stdout or "(none)\n", end="")
        # A file that left the list may be the product's own now (its CODEOWNERS): it is removed only when the loop's
        # repository no longer holds it at all.
        removed = [n for n in old_names if n not in set(new_names) and not (Path(tmp) / "new" / "tree" / n).exists()]
        for name in old_names:
            if name not in set(new_names) and name not in removed:
                print(f"kept {name}: it left the list, and the loop's repository still holds a file there")
                result.kept.append(name)
        for name in removed:
            (root / name).unlink(missing_ok=True)
            print(f"removed {name}")
            result.touched.append(name)
        for name in new_names:
            source, target = Path(tmp) / "new" / "tree" / name, root / name
            if not same(source, target):
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                print(f"copied {name}")
                result.touched.append(name)
    return result


def bump(root: Path, wanted: str) -> int:
    """In a worktree: the copy moves to `wanted`; nothing is committed."""
    run = subprocess.run(["git", "branch", "--show-current"], cwd=root, capture_output=True, text=True, check=False)
    if run.stdout.strip() in ("", "main"):
        raise SyncError("run it on a branch of a worktree: mise run start abc-123-bump-the-loop")
    repository, old = read_pin(root)
    loop = Loop(repository)
    new = loop.resolve(wanted)
    result = apply(root, loop, old, new)
    write_pin(root, new)
    print(f"{PIN}: {new}" if new != old else f"the pin stands at {new}: {len(result.touched)} files changed")
    return 0


def sh(cwd: Path, *cmd: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=False)
    except OSError as err:
        return subprocess.CompletedProcess(cmd, 127, "", f"{cmd[0]}: {err.strerror}")


def last(run: subprocess.CompletedProcess[str]) -> str:
    return (run.stderr.strip().splitlines() or ["no message"])[-1]


def message(repository: str, old: str, new: str, result: Result) -> str:
    """The text of the commit and of the pull request: the commit range, the commit list, the changed files."""
    lines = [f"The loop's files move from {old[:12]} to {new[:12]} ({repository}).", "", "Commits:",
             result.commits.rstrip("\n") or "(none)", "", "Changed files:", *(f"- {n}" for n in [PIN.as_posix(), *result.touched])]
    if result.kept:
        lines += ["", f"{KEPT}:", *(f"- {n}" for n in result.kept)]
    return "\n".join(lines) + "\n"


def stopped(done: list[str], next_step: str) -> int:
    print(f"loop: done so far: {', '.join(done)}. The worktree stays as it is.", file=sys.stderr)
    print(f"loop: next: {next_step}", file=sys.stderr)
    return 1


def bump_pr(root: Path, ref: str) -> int:
    """From the main checkout: the whole bump as one pull request, with no issue."""
    pulled = sh(root, "git", "pull", "--ff-only", "-q")
    if pulled.returncode != 0:
        raise SyncError(f"git pull --ff-only failed in the main checkout: {last(pulled)}")
    repository, old = read_pin(root)
    loop = Loop(repository)
    new = loop.resolve(ref)
    if new == old:
        print(f"the pin stands at {new[:12]}: nothing to do")
        return 0
    name = f"loop-bump-{new[:12]}"
    tree = root.resolve() / ".claude" / "worktrees" / name
    if tree.exists():
        raise SyncError(f"{tree} exists: finish that bump there (push it and open its pull request), or remove the worktree")
    made = sh(root, "git", "worktree", "add", "-q", "-b", name, str(tree), "origin/main")
    if made.returncode != 0:
        raise SyncError(f"git worktree add failed: {last(made)}")
    done = [f"the branch {name} and its worktree {tree}"]
    try:
        result = apply(tree, loop, old, new)
        write_pin(tree, new)
    except (SyncError, OSError) as err:
        return stopped(done, f"the copy failed ({err}): remove the worktree (`git worktree remove --force {tree}` and `git branch -D "
                       f"{name}`) and run the command again")
    text = message(repository, old, new, result)
    committed = sh(tree, "git", "add", "-A", "--", PIN.as_posix(), *result.touched)
    if committed.returncode == 0:
        committed = sh(tree, "git", "commit", "-q", "-m", f"Bump the loop to {new[:12]}\n\n{text}")
    if committed.returncode != 0:
        return stopped(done, f"the commit failed ({last(committed)}): fix it in {tree}, commit, then push the branch")
    done.append("the commit")
    pushed = sh(tree, "git", "push", "-q", "-u", "origin", name)
    if pushed.returncode != 0:
        return stopped(done, f"the push failed ({last(pushed)}): run `git -C {tree} push -u origin {name}`, then open the "
                       f"pull request (`gh pr create --head {name}` in {tree})")
    done.append("the push")
    with tempfile.TemporaryDirectory() as tmp:
        body = Path(tmp) / f"{name}-body.md"
        body.write_text(text + "\nA pure bump: no issue (the loop's files only). The owner reads the diff and merges.\n")
        opened = sh(tree, "gh", "pr", "create", "--head", name, "--base", "main", "--title", f"Bump the loop to {new[:12]}",
                    "--body-file", str(body))
    if opened.returncode != 0:
        return stopped(done, f"the pull request failed ({last(opened)}): run `gh pr create --head {name}` in {tree}")
    print(opened.stdout.strip().splitlines()[-1] if opened.stdout.strip() else f"pull request opened for {name}")
    return 0


def matches(path: str, pattern: str) -> bool:
    """Whether the list's `pattern` names `path`: the rules of `.github/leak_guard.py` (a folder names every file beneath it, hidden
    ones too; `*` and `**` skip a name that starts with a dot)."""

    def walk(pat: list[str], got: list[str]) -> bool:
        if not pat:
            return True  # the pattern named a folder: everything beneath it
        if not got:
            return False
        head, rest = pat[0], pat[1:]
        if head == "**":
            return walk(rest, got) or (not got[0].startswith(".") and walk(pat, got[1:]))
        if got[0].startswith(".") and not head.startswith("."):
            return False
        return fnmatch.fnmatchcase(got[0], head) and walk(rest, got[1:])

    return walk([p for p in pattern.split("/") if p], path.split("/"))


def listed_path(path: str, list_text: str) -> bool:
    """Whether the list (the text of `.workflow/loop-files.txt`) names `path`: its rules apply in order, `!` takes files out."""
    named = False
    for raw in list_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        out = line.startswith("!")
        if matches(path, line[1:].strip() if out else line):
            named = not out
    return named


def pure(paths: list[str], list_text: str, old_text: str = "", removed: Collection[str] = ()) -> list[str]:
    """The paths of a diff that make it more than a pure bump: not the pin, not a file that the new list names, and not a
    file that only the old list named when the diff deletes it (a bump removes a file that left the loop: an edit of such a
    file is no bump). `removed` holds the paths that the diff deletes."""
    return [p for p in paths if p != PIN.as_posix() and not listed_path(p, list_text)
            and not (p in removed and listed_path(p, old_text))]


def read_status(lines: list[str]) -> tuple[list[str], set[str]]:
    """The paths and the deleted paths of `git diff --name-status --no-renames` lines (`D<TAB>path`) or of the files API
    (`removed<TAB>path`: its `status` and `filename`). A line with no tab is a
    path with no status: it is not deleted."""
    paths, removed = [], set()
    for line in lines:
        status, _, path = line.rpartition("\t")
        paths.append(path)
        if status in ("D", "removed"):
            removed.add(path)
    return paths, removed


def main(argv: list[str]) -> int:
    try:
        if argv[:1] == ["check"] and len(argv) == 1:
            return check(Path("."))
        if argv[:1] == ["bump"] and len(argv) <= 2 and not (argv[1:2] or ["x"])[0].startswith("-"):
            if sh(Path("."), "git", "branch", "--show-current").stdout.strip() == "main":
                return bump_pr(Path("."), argv[1] if len(argv) == 2 else "main")
            if len(argv) == 2:
                return bump(Path("."), argv[1])
            raise SyncError("on a branch, name the commit: mise run loop:bump COMMIT")
        if argv[:1] == ["pure"] and len(argv) in (2, 3):
            try:
                texts = [Path(a).read_text() for a in argv[1:]]
            except OSError as err:
                raise SyncError(f"a list file cannot be read ({err.strerror})") from None
            paths, removed = read_status([line for line in sys.stdin.read().splitlines() if line])
            extra = pure(paths, *texts, removed=removed)
            for path in extra:
                print(path)
            return 1 if extra else 0
    except SyncError as err:
        print(f"loop: {err}", file=sys.stderr)
        return 1
    print(__doc__.split("\n\n")[1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
