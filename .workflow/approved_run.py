#!/usr/bin/python3 -I
"""Run a script of the owner's only after the owner read what changed in it since the last yes.

The owner's call, 2026-11-23 (DECISIONS.md "Delivery"): `orchestrator/deploy/` and `.workflow/agent_user.py` merge on the
reviewer's verdict, and the owner checks them when they run, not when they merge. A merge runs none of their code with
more rights than the agents have: it runs only when the owner runs a command (as root, or as the owner). So this
launcher stands between the owner and those scripts. It shows the commits and the diff of each guarded file since the
commit of the owner's last yes, and runs the script only after a new yes. With no change since the last yes it runs
with no question. A no runs nothing and records nothing. This file stays the owner's (CODEOWNERS): it is the one file
that guards root's code, and `install.py` cannot check itself (a changed `install.py` could skip showing its own diff).

    /usr/bin/python3 -I .workflow/approved_run.py orchestrator/deploy/install.py -- [check | restart | unlock | set | profile | pause | resume | --config F]
    sudo /usr/bin/python3 -I .workflow/approved_run.py .workflow/agent_user.py -- admin | check
    /usr/bin/python3 -I .workflow/approved_run.py .workflow/agent_user.py -- models

The owner types these through `owlcode` (bin/owlcode, OWL-54): `owlcode install`, `check`, `restart`, `set`, `profile`, `pause`,
`resume`, `unlock`, `models` and `admin`.

The record of the yes is STATE, a JSON file that only root can write, outside the repository: for each guarded file the
commit that the owner approved and the date. The launcher refuses a STATE (or a folder of it) that another user owns or
that its group or other users can write: code that runs as the owner (a merged npm script, a test) could otherwise
write a yes. A yes from the owner's account (no sudo) writes STATE through `sudo`, with WRITE_STATE, a constant of this
process: never a script of the checkout.

What runs is what was shown. The diff comes from git's objects, so each covered file must be equal to HEAD's copy in
the checkout before the diff (an edit that is not committed is refused, not run). Right before the run the launcher
reads each covered file again and compares its SHA-256 with the shown copy: a file that changed after the diff runs
nothing. The script runs from the checkout (its `__file__` finds its sibling files there); `install.py` then reads
`service.py` and `agent_user.py` from the checkout to copy them, which is why SCRIPTS gives it all three to show and
check. That last read is the owner's own checkout, closed to the agents (the owner's home is 700).

The printed text shows each control character and each invisible format character (an escape sequence, a carriage
return, a bidi override) as a visible escape: a diff that moves the cursor could hide a line from the owner.

Standard library only; Python 3.9 (`/usr/bin/python3`) and newer.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import unicodedata
from pathlib import Path
from collections.abc import Callable, Mapping, Sequence
from typing import NamedTuple

REPO = Path(__file__).resolve().parent.parent


NAME_RULE = re.compile(r"[a-z]([a-z0-9-]{0,30}[a-z0-9])?")
NAME_RULE_TEXT = "lowercase letters and digits, dashes between them, a letter first, 32 characters at most"


def origin_name(checkout: Path) -> str:
    """The name of the repository that the `origin` URL of `checkout` names, or "" when there is none. It reads git's files
    and runs no git: as root, this launcher runs nothing of the owner's checkout (its config can name a program). It is
    agent_user.py's rule for the agents' user name, written again here: the launcher never imports the file it guards."""
    git_path = checkout / ".git"
    try:
        if git_path.is_file():  # a worktree: `.git` names its own folder, and that folder's `commondir` the shared one
            folder = Path(git_path.read_text().split("gitdir:", 1)[1].strip())
            folder = folder if folder.is_absolute() else checkout / folder
            common = folder / "commondir"
            git_path = folder / common.read_text().strip() if common.is_file() else folder
        config = (git_path / "config").read_text()
    except (OSError, IndexError):
        return ""
    section = re.search(r'^\s*\[remote "origin"\]\s*$(.*?)(?=^\s*\[|\Z)', config, re.M | re.S)
    url = re.search(r"^\s*url\s*=\s*(\S+)", section.group(1), re.M) if section else None
    if not url:
        return ""
    path = url.group(1).rstrip("/")
    path = path[:-len(".git")] if path.endswith(".git") else path
    return re.split(r"[/:]", path)[-1]


def state_path(environ: Mapping[str, str], checkout: Path) -> Path:
    """The record's file is `/var/db/NAME-approved-runs.json`. NAME is AGENT_USER (with sudo: `sudo AGENT_USER=NAME ...`),
    else the name of the repository that the checkout's `origin` names: agent_user.py's name for the agents' user."""
    name = environ.get("AGENT_USER") or origin_name(checkout)
    if not name:
        sys.exit("approved_run.py: no name for the record: the checkout has no `origin` remote; set AGENT_USER")
    if not NAME_RULE.fullmatch(name):  # agent_user.py's rule: a name that it refuses names no record
        sys.exit(f"approved_run.py: {name!r} cannot name the record: {NAME_RULE_TEXT}")
    return Path(f"/var/db/{name}-approved-runs.json")


STATE = state_path(os.environ, REPO)
ROOT_UID = 0
COMMIT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


class Script(NamedTuple):
    python: str  # the interpreter the script needs
    covers: tuple[str, ...]  # the files of the checkout that a run of the script reads: each one is shown and checked
    as_root: bool  # the script runs root's steps (sudo) itself


# The guarded files and how each script runs. A change to this list is the owner's, as this file is.
SCRIPTS: dict[str, Script] = {
    # install copies service.py and agent_user.py into root's installed folder: their diffs come with install's own
    "orchestrator/deploy/install.py": Script("/usr/local/bin/python3", ("orchestrator/deploy/install.py",
                                                                        "orchestrator/deploy/service.py",
                                                                        ".workflow/agent_user.py"), True),
    ".workflow/agent_user.py": Script("/usr/bin/python3", (".workflow/agent_user.py",), False),
}
GUARDED = tuple(sorted({path for script in SCRIPTS.values() for path in script.covers}))

# The root step that records a yes, run as `[sudo] /usr/bin/python3 -I -c WRITE_STATE PATH` with the JSON on its standard
# input. It writes a new file beside PATH and renames it over PATH: a reader sees the old file or the new one. A file that
# root makes in a folder gets that folder's group (BSD), so it needs no chown; mode 644 lets the owner read it.
WRITE_STATE = """\
import json, os, sys, tempfile
path = sys.argv[1]
text = sys.stdin.read()
json.loads(text)
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".approved-runs-")
with os.fdopen(fd, "w") as f:
    f.write(text)
os.chmod(tmp, 0o644)
os.replace(tmp, path)
"""


class Refused(Exception):
    """A step that cannot go on: the message says why and what to do. Nothing ran."""


def visible(data: bytes) -> str:
    """The text with each control character (tab and newline apart) and each invisible format character as an escape."""
    out = []
    for ch in data.decode("utf-8", errors="backslashreplace"):
        if ch in "\n\t" or unicodedata.category(ch) not in ("Cc", "Cf", "Zl", "Zp"):
            out.append(ch)
        else:
            out.append(f"\\x{ord(ch):02x}" if ord(ch) < 0x100 else f"\\u{ord(ch):04x}")
    return "".join(out)


class Git:
    """git in the checkout, read-only. As root (under sudo) git refuses a repository that another user owns, so the
    checkout is named safe for this one command. No program that the checkout's config or attributes name runs: no pager,
    no fsmonitor, no external diff, no textconv, and no `gpg.program` (`log.showSignature` would run it as root before
    the question). `--text` prints the lines of a file that an attribute marks binary (`-diff`), so an
    attribute cannot hide a diff."""

    def __init__(self, repo: Path) -> None:
        self.repo = repo

    def run(self, *args: str, ok: Sequence[int] = (0,)) -> tuple[int, bytes]:
        cmd = ["git", "-C", str(self.repo), "-c", f"safe.directory={self.repo}", "-c", "core.fsmonitor=false",
               "-c", "log.showSignature=false", "--no-pager", *args]
        done = subprocess.run(cmd, capture_output=True, check=False)
        if done.returncode not in ok:
            raise Refused(f"git {' '.join(args)} failed: {done.stderr.decode(errors='replace').strip()}")
        return done.returncode, done.stdout

    def out(self, *args: str) -> bytes:
        return self.run(*args)[1]

    def head(self) -> str:
        return self.out("rev-parse", "--verify", "HEAD^{commit}").decode().strip()

    def blob(self, commit: str, path: str) -> bytes:
        return self.out("cat-file", "blob", f"{commit}:{path}")

    def has(self, commit: str) -> bool:
        return self.run("cat-file", "-e", f"{commit}^{{commit}}", ok=(0, 1, 128))[0] == 0

    def changed(self, old: str, new: str, path: str) -> bool:
        return self.run("diff", "--quiet", "--no-ext-diff", "--no-textconv", old, new, "--", path, ok=(0, 1))[0] == 1

    def count(self, old: str, new: str, path: str) -> int:
        return int(self.out("rev-list", "--count", f"{old}..{new}", "--", path).decode().strip())

    def log(self, old: str, new: str, path: str) -> bytes:
        return self.out("log", "--oneline", "--no-decorate", "--no-color", "--no-show-signature", f"{old}..{new}", "--", path)

    def diff(self, old: str, new: str, path: str) -> bytes:
        return self.out("diff", "--no-color", "--no-ext-diff", "--no-textconv", "--text", old, new, "--", path)


def only_root_writes(st: os.stat_result, uid: int) -> bool:
    return st.st_uid == uid and not st.st_mode & 0o022


def read_state(path: Path, uid: int = ROOT_UID) -> dict[str, dict[str, str]]:
    """The last yes of each file, from a file that only `uid` (root) can write; {} when there is none yet."""
    try:
        folder = os.stat(path.parent)
    except OSError:
        raise Refused(f"cannot read the folder {path.parent} of the record of your yes answers: nothing ran") from None
    if not stat.S_ISDIR(folder.st_mode) or not only_root_writes(folder, uid):
        raise Refused(f"{path.parent} must be a folder that only root can write (owner uid {folder.st_uid}, "
                      f"mode {stat.S_IMODE(folder.st_mode):o}): nothing ran")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return {}
    except OSError as e:
        raise Refused(f"cannot open {path} without following a link ({e.strerror}): remove it with sudo rm, then run "
                      "the command again and read each whole file") from None
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode) or not only_root_writes(st, uid):
            raise Refused(f"{path} must be a file that only root can write (owner uid {st.st_uid}, mode "
                          f"{stat.S_IMODE(st.st_mode):o}): remove it with sudo rm, then run the command again and read "
                          "each whole file")
        text = f.read()
    try:
        state = json.loads(text)
        assert isinstance(state, dict)
        for name, entry in state.items():
            assert isinstance(name, str) and isinstance(entry, dict) and COMMIT.fullmatch(entry.get("commit", ""))
            assert isinstance(entry.get("date"), str)
    except (ValueError, AssertionError, TypeError):
        raise Refused(f"{path} is not a record of yes answers: remove it with sudo rm, then run the command again "
                      "and read each whole file") from None
    return state


def record_state(path: Path, state: dict[str, dict[str, str]], python: str = "/usr/bin/python3",
                 sudo: Sequence[str] | None = None) -> None:
    """Write the state as root: directly under sudo, else through `sudo` (one password prompt)."""
    prefix = list(sudo) if sudo is not None else ([] if os.geteuid() == 0 else ["sudo"])
    done = subprocess.run([*prefix, python, "-I", "-c", WRITE_STATE, str(path)],
                          input=json.dumps(state, indent=2, sort_keys=True) + "\n", text=True, check=False)
    if done.returncode:
        raise Refused(f"could not record your yes in {path}: nothing ran")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def review(git: Git, head: str, path: str, entry: dict[str, str] | None) -> bytes | None:
    """What the owner reads for one file, or None when it did not change since the last yes."""
    if entry is None:
        return (f"{path}: no yes recorded yet: the whole file at HEAD ({head[:7]})\n".encode() + git.blob(head, path))
    last, date = entry["commit"], entry["date"]
    if not git.has(last):
        return (f"{path}: your last yes ({last[:7]}, {date}) names a commit that this clone lacks: the whole file at "
                f"HEAD ({head[:7]})\n").encode() + git.blob(head, path)
    if not git.changed(last, head, path):
        return None
    n = git.count(last, head, path)
    log = b"".join(b"  " + line + b"\n" for line in git.log(last, head, path).splitlines())
    return (f"{path}: {n} commit{'' if n == 1 else 's'} since your last yes ({last[:7]}, {date})\n".encode() + log
            + git.diff(last, head, path))


def ask_terminal(prompt: str) -> str:
    if not sys.stdin.isatty():
        raise Refused("a guarded file changed since your last yes: run the command at a terminal, read the diff and "
                      "answer: nothing ran")
    return input(prompt)


def main(argv: Sequence[str], repo: Path = REPO, state_path: Path = STATE, uid: int = ROOT_UID,
         ask: Callable[[str], str] = ask_terminal, out: Callable[[str], None] = print,
         record: Callable[[Path, dict[str, dict[str, str]]], None] = record_state,
         execv: Callable[[str, list[str]], None] = os.execv, today: str | None = None) -> int:
    usage = f"usage: approved_run.py FILE [-- ARGS...], FILE one of: {', '.join(SCRIPTS)}"
    if not argv or argv[0] not in SCRIPTS or (len(argv) > 1 and argv[1] != "--"):
        out(usage)
        return 2
    name, args = argv[0], list(argv[2:])
    script = SCRIPTS[name]
    try:
        git = Git(repo)
        head = git.head()
        shown = {}
        for path in script.covers:
            blob = git.blob(head, path)
            if (repo / path).read_bytes() != blob:
                raise Refused(f"{path} differs from HEAD ({head[:7]}): commit it or restore it. Only committed code is "
                              "shown and run: nothing ran")
            shown[path] = digest(blob)
        state = read_state(state_path, uid)
        reviews = [text for text in (review(git, head, path, state.get(path)) for path in script.covers) if text]
        if reviews:
            for text in reviews:
                out(visible(text).rstrip("\n"))
            as_root = script.as_root or os.geteuid() == 0
            if ask(f"Run this version{' as root' if as_root else ''}? [y/N] ").strip().lower() not in ("y", "yes"):
                out("No: nothing ran, and your last yes stays as it was.")
                return 1
            date = today or datetime.date.today().isoformat()
            record(state_path, {**state, **{path: {"commit": head, "date": date} for path in script.covers}})
        for path in script.covers:  # right before the run: the bytes on disk are the bytes shown
            if digest((repo / path).read_bytes()) != shown[path]:
                raise Refused(f"{path} changed after its diff was shown: nothing ran. Run the command again")
    except Refused as e:
        out(str(e))
        return 1
    sys.stdout.flush()
    try:
        execv(script.python, [script.python, "-I", str(repo / name), *args])
    except OSError as e:
        out(f"cannot run {script.python} ({e.strerror}): {name} needs it")
        return 1
    return 0  # only a test's execv returns


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
