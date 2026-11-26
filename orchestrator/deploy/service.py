#!/usr/bin/python3
"""The service's first step: wait until a check command succeeds, then run the orchestrator in this process's place.

launchd starts it from the LaunchAgent that `install.py` loads into the agents' user domain. After a restart of the Mac the
agent's keychain stays locked until the owner types its password once in a pane on the agents' user: that domain
has the panes' security session, so the unlock reaches this process (a LaunchDaemon's session never got it). The
orchestrator cannot read its keys before that. So this step runs the check (a keychain lookup that reads the key with `-w`: without it, a locked keychain answers too) every `--period` seconds and writes one `waiting:` line each time,
until the check passes: the service never restarts in a loop, and `orchestrator.py --check` reads the newest line. With
`--log FILE` each line goes to the end of FILE, opened again for each line as the orchestrator's `--log` does,
so a rotation of the log loses none; without it the lines go to standard output. Standard library only; it runs on macOS's
/usr/bin/python3 (3.9), so nothing newer than 3.9 is used here.

A Background LaunchAgent in /Library/LaunchAgents may load into every user's domain at login, the owner's too, and into root's `user/0` at boot. With
`--user NAME` the service runs only as NAME: as another user it writes one line and idles, so launchd has no exit to restart
and nothing reads the agents' keys; `install.py check` fails while the job is loaded, or not disabled, in the owner's domain or root's; the install disables it there.

    service.py --period 60 --user NAME --why "the agent's keychain is locked in the service's session ..." --log FILE --check "security find-generic-password ... -w ..." -- CMD ARGS...

Each team's Linear key: a Linear key reaches one workspace, so a team in a second workspace has a key of its own,
in a variable that its `linear_key_env` names. `agent_user.py run` gives the process only LINEAR_API_KEY, and it clears the
key variables of its caller. So the service runs this script a second time inside `agent_user.py run`, with no `--check` (it
waits for nothing) and one `--env-from VAR=LOOKUP` for each key besides LINEAR_API_KEY: LOOKUP is a keychain command in
`-w` form. This script runs it, puts its output in the environment as VAR and hands the process to the command. The value
goes to no file and no line. A lookup that fails (no item, no output) leaves VAR out and writes one line that names VAR:
the orchestrator then reports that team's key as not set, and only that team's round fails.

    service.py --log FILE --env-from ABC_LINEAR_API_KEY="security find-generic-password ... -w ..." -- CMD ARGS...
"""

from __future__ import annotations

import argparse
import os
import pwd
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from collections.abc import Callable, MutableMapping, Sequence

WAITING = "waiting:"  # orchestrator.py --check reads this word
VARIABLE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def wait_then_run(check: Sequence[str], command: Sequence[str], period: float, why: str,
                  run: Callable[[Sequence[str]], int], sleep: Callable[[float], None],
                  execute: Callable[[Sequence[str]], None], out: Callable[[str], None]) -> None:
    """Run `check` until it exits 0, one waiting line per try, then hand the process to `command`."""
    while True:
        code = run(check)
        if code == 0:
            break
        out(f"{stamp()} {WAITING} {why} (the check exited {code}); next try in {int(period)} s")
        sleep(period)
    out(f"{stamp()} starting the orchestrator")
    execute(command)


def env_from(specs: Sequence[str], lookup: Callable[[Sequence[str]], tuple[int, str]],
             environ: MutableMapping[str, str], out: Callable[[str], None]) -> None:
    """Put the output of each lookup into `environ`, one variable for each `VAR=LOOKUP` of `specs`. A lookup that
    fails or prints nothing leaves its variable out, and one line names the variable and the exit code, never an output."""
    for spec in specs:
        name, _, command = spec.partition("=")
        code, value = lookup(shlex.split(command))
        if code == 0 and value:
            environ[name] = value
        else:
            environ.pop(name, None)  # a value the caller passed on must not stand in for the key
            out(f"{stamp()} no key for {name}: its lookup exited {code}" if code else f"{stamp()} no key for {name}: its lookup printed nothing")


def idle_as_other_user(expected: str, actual: str, out: Callable[[str], None], sleep: Callable[[float], None]) -> None:
    """The job was loaded into the domain of a user other than `expected`. Write one line, then idle: an exit would
    make launchd restart the job in a loop (KeepAlive), and the check, the keychain and the orchestrator must not run."""
    out(f"{stamp()} refused: this service runs only as {expected}, not as {actual}; idling (unload it: launchctl bootout)")
    while True:
        sleep(3600)


def run_quiet(cmd: Sequence[str]) -> int:
    """The check's exit code; its output goes nowhere (a keychain lookup with -w prints the key: it must reach no file)."""
    return subprocess.run(list(cmd), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode


def lookup_key(cmd: Sequence[str]) -> tuple[int, str]:
    """A lookup's exit code and its output without the trailing newline. Its error output goes nowhere."""
    done = subprocess.run(list(cmd), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    return done.returncode, done.stdout.decode(errors="replace").strip()


def execute(cmd: Sequence[str]) -> None:
    os.execvp(cmd[0], list(cmd))


def say(line: str) -> None:
    print(line, flush=True)


def append_to(path: str) -> Callable[[str], None]:
    """A line writer that opens `path` for each line (a rotation renames the file: the next line goes to the new one)."""
    def write(line: str) -> None:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError as e:  # never a crash loop for a log: the line goes to standard output, the service's crash file
            say(f"log not written ({type(e).__name__}): {line}")
    return write


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--" not in args:
        print("service.py: name the command after --", file=sys.stderr)
        return 2
    split = args.index("--")
    parser = argparse.ArgumentParser(description="Wait until a check passes, then run a command in this process's place.")
    parser.add_argument("--check", help="the check, one shell-quoted string; with none, nothing is waited for")
    parser.add_argument("--env-from", action="append", default=[], metavar="VAR=LOOKUP",
                        help="put the output of the keychain command LOOKUP into the environment as VAR; repeatable")
    parser.add_argument("--why", default="the check fails", help="what a failed check means, for the waiting line")
    parser.add_argument("--period", type=float, default=60.0)
    parser.add_argument("--user", help="run only as this user; as another user, write one line and idle")
    parser.add_argument("--log", help="append each line to this file, opened again for each line")
    opts = parser.parse_args(args[:split])
    command = args[split + 1:]
    if not command:
        print("service.py: name the command after --", file=sys.stderr)
        return 2
    out = append_to(opts.log) if opts.log else say
    me = pwd.getpwuid(os.getuid()).pw_name
    if opts.user and me != opts.user:
        idle_as_other_user(opts.user, me, out, time.sleep)
    for spec in opts.env_from:
        name, equals, lookup = spec.partition("=")
        if not equals or not VARIABLE.fullmatch(name) or not lookup.strip():
            print(f"service.py: --env-from takes VAR=LOOKUP, VAR in capital letters, digits and _ (got {name!r})", file=sys.stderr)
            return 2
    if opts.env_from:
        env_from(opts.env_from, lookup_key, os.environ, out)
    if opts.check:
        wait_then_run(shlex.split(opts.check), command, opts.period, opts.why, run_quiet, time.sleep, execute, out)
    else:
        execute(command)  # no line: the outer step wrote "starting the orchestrator", and a `check` or a dry round must print only its own lines
    return 0  # not reached: execute replaced the process


if __name__ == "__main__":
    sys.exit(main())
