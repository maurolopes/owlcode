#!/usr/local/bin/python3
"""Install the orchestrator as a service. The owner runs it from the owner's account, with sudo.

The owner's calls, 2026-11-09 (DECISIONS.md "Our own orchestrator ..."): `orchestrator/` merges on the verdict, so the owner's
check is this install, not the merge. This file cannot check itself (a changed copy could skip its own diff), so the owner
runs it through the launcher `.workflow/approved_run.py` (the owner's call, 2026-11-23): it shows what changed in
this file, `service.py` and `agent_user.py` since the owner's last yes and asks one yes first. Step (1) below stays: it shows
the commits of `orchestrator.py` and the other installed files too. The service runs a copy owned by root that the agents' user can read but not write,
never the checkout, from a plist owned by root. Never a plist in that user's home: the agents could edit it, and so point
the service at other code.

The service is a LaunchAgent (`/Library/LaunchAgents/LABEL.plist`) loaded into the agents' user domain, `user/UID`, with
`LimitLoadToSessionType` Background (the owner's call, 2026-11-18). macOS keeps a keychain's unlocked state per
security session. A LaunchDaemon (the system domain, with `UserName`) is in a session that no unlock reaches: the first
install's service waited for the keychain with no end (measured 2026-11-18). Without that session type, `launchctl bootstrap
user/UID` refuses the plist (`5: Input/output error`): the agents' user has no GUI session. The user domain exists only after
the first SSH login after a restart, and launchd loads the job into it at that login. The domain keeps that login's audit
session, and each later SSH login (a herdr pane) has a session of its own, so the password typed in a pane does not reach
the service (measured 2026-11-19 with `launchctl procinfo`). `unlock` (`owlcode unlock`, from the
owner's account after each restart) runs `security unlock-keychain` through `launchctl asuser UID`, in the domain's own
session.

The steps: (0) show the changes to the installed config as one diff: the retired keys that go (`RETIRED`) and the keys that
the example has and the config lacks, (1) print the commits under
the installed files since the installed version (when that version is no commit of the checkout: the diff of each
installed file against the checkout's), (2) check the Python, (3) copy the new files to a staging folder and run
one `--once --dry-run` round of the installed code on the installed config and one of the new code on a staged copy of the
config that holds the changes (the installed code calls a new key unknown), side by side, (4) ask for one yes,
(5) after the yes write the changes to the installed config, swap the staging folder in, record the commit, write the
newsyslog entry and the LaunchAgent, remove the LaunchDaemon of an older install, and load the agent again. A no, or a
failed round, leaves the installed config as it was.

The log: launchd opens `StandardOutPath` once, at the start, and macOS's newsyslog renames a log and makes a new
one (no copytruncate). So service.py and orchestrator.py write the log themselves (`--log`), each line with the file opened
again, and launchd's own output (what a start that stops prints) goes to a second file, `crash_log`. The newsyslog entry
(`/etc/newsyslog.d/LABEL.conf`, root's) rotates both by size with the flag N (no signal: no process needs one).

Each team's Linear key: `agent_user.py run` gives the process LINEAR_API_KEY only. A team that names another
variable in `linear_key_env` (a team in another Linear workspace) gets its key in a second step: the plist and
`as_agent` run `service.py --env-from VAR=LOOKUP` inside `agent_user.py run`, where LOOKUP is the config's `keychain_check`
with the item of that variable (`key_item`: ABC_LINEAR_API_KEY is the item `abc-linear-key`). With no such team the plist
is as it was. `check` shows the orchestrator's `keys:` line of each team, and a line for the plist's lookup of each key.

The settings are the `[service]` table of the orchestrator's config file, outside the repository (the owner copies
`orchestrator.example.toml` there once). Standard library only; Python 3.11 or newer (tomllib).

    owlcode install [--config /usr/local/etc/orchestrator.toml]
    owlcode check     # the installed copy's `orchestrator.py --check`, then the install's own properties, then each
                      # product's agents' user: it exists, its home is mode 700, it holds no other team's clone, and
                      # each profile's key_check of the cells passes as that user
    owlcode set KEY=VALUE ...   # a top-level key, checked on a copy (preflight, dry round), then a restart
    owlcode profile [NAME]   # the profiles with models and prices; the one you pick goes to `inference_profile`, as `set` does it
    owlcode pause     # no new starts until `owlcode resume`
    owlcode resume    # end the pause
    owlcode restart   # one dry round on the config as it is, then a restart of the service, then `check`

Each owlcode command (bin/owlcode) runs `/usr/bin/python3 -I .workflow/approved_run.py orchestrator/deploy/install.py -- [install | check | restart | unlock | set | profile | pause | resume]`,
which runs `/usr/local/bin/python3 -I orchestrator/deploy/install.py ...` after the yes.
"""

from __future__ import annotations

import argparse
import difflib
import json
import plistlib
import re
import shlex
import subprocess
import sys
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# What the service runs, as paths in the checkout -> paths in the installed folder.
FILES = {
    "orchestrator/orchestrator.py": "orchestrator.py",
    "orchestrator/model-catalog.json": "model-catalog.json",
    "orchestrator/deploy/service.py": "service.py",
    ".github/linear.py": ".github/linear.py",  # the config's `linear.script` is ".github/linear.py", from the installed folder
    ".workflow/agent_user.py": "agent_user.py",  # `run`: the Linear key from the keychain, bin/gh (GH_SHIM) first on PATH
}
# The installed copy's own gh shim, root's: `agent_user.py run` puts the `bin` beside it first on PATH, so the
# service's gh runs the installed agent_user.py, never the agent's ~/.local/bin/gh. `${0%/*}` is the shim's own folder (the
# kernel passes a script's full path), so the staged copy's dry round runs the staged agent_user.py.
GH_SHIM = '#!/bin/sh\nexec /usr/bin/python3 -I "${0%/*}/../agent_user.py" gh "$@"\n'
# The local review's model and effort level of each `[profiles.NAME]` table, as `.workflow/agent_user.py` reads them:
# that script builds LOCAL_REVIEW_COMMAND and runs on Python 3.9, which reads no TOML. `install` and `restart` write the file
# beside the installed copy, root's. The settings' one place is the config. Each profile's entry also lists the profiles that
# its cells run on, itself first (`cells`): `paseo-daemon` gives Paseo's daemon the keys of each.
REVIEW_FILE = "local-review.json"
# The config's `inference_profile`, for the same script: the service starts Paseo's daemon through `agent_user.py
# paseo-daemon`, which takes the profile from this file, and `install` and `restart` write it beside REVIEW_FILE.
PROFILE_FILE = "inference-profile.json"
CELLS = ("neither", "intricate", "judgment", "both")  # the cells of each [profiles.NAME] table (the orchestrator's CELLS)
REVIEW_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/:@-]{0,63}")  # as the orchestrator's SETTING_SHAPE: it reaches a shell command
PYTHON_CHECK = ("import sys, urllib.error, urllib.request\n"
                "assert sys.version_info >= (3, 11), sys.version\n"
                "try:\n    urllib.request.urlopen('https://api.linear.app/graphql', timeout=15)\n"
                "except urllib.error.HTTPError:\n    pass  # an answer: the TLS handshake passed\n")


# The root step that gives a file to a user, run as `sudo /usr/bin/python3 -I -c GIVE_FILE PATH USER`. It
# opens each folder of PATH from `/` without following a link (`O_NOFOLLOW | O_DIRECTORY`, each one relative to the one
# before), so a symlink in any folder of the path is refused, not only one at the end: the agents' user owns `~/Library` and
# can swap it for a link to another folder. It opens the log the same way, checks the open descriptor (a regular file with one
# link) and changes that descriptor's owner: the check and the change are on the same file, so a swap of the path in between
# changes nothing. A file with one link has no other name, so it cannot be a root file that the agents' user linked. It prints
# `missing`, `refused: WHY` or `done`.
GIVE_FILE = """\
import os, pwd, stat, sys
path, user = sys.argv[1:3]
parts = [p for p in path.split("/") if p]
if not path.startswith("/") or not parts:
    sys.exit(print("refused: the path is not absolute"))
fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)  # the process ends at once, so no descriptor is closed by hand
for part in parts[:-1]:
    try:
        fd = os.open(part, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY, dir_fd=fd)
    except FileNotFoundError:
        sys.exit(print("missing"))
    except OSError as e:
        sys.exit(print("refused: a folder of the path is a link or not a folder (" + e.strerror + ")"))
try:
    log = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
except FileNotFoundError:
    sys.exit(print("missing"))
except OSError as e:
    sys.exit(print("refused: cannot open it without following a link (" + e.strerror + ")"))
st = os.fstat(log)
if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
    sys.exit(print("refused: not a regular file with one link"))
os.fchown(log, pwd.getpwnam(user).pw_uid, -1)
print("done")
"""


class Refused(Exception):
    """A step that cannot go on: the message says why and what to do."""


@dataclass(frozen=True)
class ProjectUser:
    """A product and the agents' user where its sessions run (OWL-62, design b2: one user for each project): the
    `agent_user` of its table, else `[service] user`."""

    team: str
    repo: str
    user: str


@dataclass(frozen=True)
class Service:
    user: str
    install_dir: Path
    python: Path
    label: str
    log_file: Path
    keychain_check: str
    config: Path
    keys: tuple[str, ...] = ()  # the variable of each team's Linear key besides LINEAR_API_KEY, in the config's order
    projects: tuple[ProjectUser, ...] = ()  # each product and its agents' user, the first product first

    @property
    def stage(self) -> Path:
        return self.install_dir.with_name(self.install_dir.name + ".new")

    @property
    def old(self) -> Path:
        return self.install_dir.with_name(self.install_dir.name + ".old")

    @property
    def plist(self) -> Path:
        return Path("/Library/LaunchAgents") / f"{self.label}.plist"

    @property
    def daemon_plist(self) -> Path:
        """The LaunchDaemon of an older install, which ran in a session that no unlock reaches: the install removes it, and `check` fails while it is there."""
        return Path("/Library/LaunchDaemons") / f"{self.label}.plist"

    @property
    def crash_log(self) -> Path:
        """launchd's own output beside the log: what a start that stops prints (a config error, a traceback), as
        `orchestrator.crash.log`."""
        return self.log_file.with_name(f"{self.log_file.stem}.crash{self.log_file.suffix}")

    @property
    def rotation(self) -> Path:
        return Path("/etc/newsyslog.d") / f"{self.label}.conf"


DEFAULT_KEY_VARIABLE = "LINEAR_API_KEY"  # orchestrator.py DEFAULT_LINEAR_KEY_ENV: the key that `agent_user.py run` gives every process
KEY_VARIABLE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")


def team_keys(data: dict[str, Any], config: Path | None = None) -> list[tuple[str, str]]:
    """The (team, variable) of each team's Linear key: the config's own team first, then each `[[product]]`; a team
    with no `linear_key_env` has LINEAR_API_KEY. The same reading as `orchestrator.py` `load_config`."""
    first = data.get("linear")
    linear: dict[str, Any] = first if isinstance(first, dict) else {}
    tables: list[tuple[str, dict[str, Any]]] = [(str(linear.get("team") or "the first team"), linear)]
    tables += [(str(t.get("team") or "?"), t) for t in data.get("product") or [] if isinstance(t, dict)]
    out = []
    for team, table in tables:
        var = table.get("linear_key_env", DEFAULT_KEY_VARIABLE)
        if not isinstance(var, str) or not KEY_VARIABLE.fullmatch(var):
            raise Refused(f"{config or 'the config'}: team {team}: linear_key_env must be the name of an environment variable "
                          "(capital letters, digits and _, a letter first)")
        out.append((team, var))
    return out


USER_NAME = re.compile(r"[a-z]([a-z0-9-]{0,30}[a-z0-9])?")  # a macOS short name, as bin/owlcode USER_RULE


def project_users(data: dict[str, Any], config: Path) -> tuple[ProjectUser, ...]:
    """Each product of the config and its agents' user: the first product (`[linear] team`, `[github] repo` and the
    top-level `agent_user` and `paseo_port`), then each `[[product]]` table with its own. A product with no `agent_user`
    has `[service] user`. `agent_user.py admin` and `paseo-daemon` use these keys; the orchestrator itself does not.
    Two users whose daemons take one port are refused: the second daemon could not listen."""
    service = (data.get("service") or {}).get("user", "")
    linear, github = data.get("linear") or {}, data.get("github") or {}
    tables = [(linear.get("team"), github.get("repo"), data)]
    tables += [(t.get("team"), t.get("repo"), t) for t in data.get("product") or [] if isinstance(t, dict)]
    out: list[ProjectUser] = []
    ports: dict[int, str] = {}
    for team, repo, table in tables:
        if not isinstance(team, str) or not team or not isinstance(repo, str) or not repo:
            continue
        user = table.get("agent_user", service)
        if not isinstance(user, str) or not USER_NAME.fullmatch(user):
            raise Refused(f"{config}: the agent_user of team {team} must be a user name of a lowercase letter, digits and dashes")
        port = table.get("paseo_port")
        if port is not None:
            if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
                raise Refused(f"{config}: the paseo_port of team {team} must be a whole number from 1024 to 65535")
            if ports.setdefault(port, user) != user:
                raise Refused(f"{config}: team {team} gives the user {user} the paseo_port {port} of the user {ports[port]}: "
                              "each user's daemon needs a port of its own")
        out.append(ProjectUser(team, repo, user))
    return tuple(out)


def key_item(variable: str) -> str:
    """The keychain item of a team's key (agent_user.py KEYS): the variable in lower case with dashes, and `-api-key` as `-key`:
    LINEAR_API_KEY is `linear-key` and ABC_LINEAR_API_KEY is `abc-linear-key`."""
    return variable.lower().replace("_", "-").replace("-api-key", "-key")


def key_lookup(svc: Service, variable: str) -> list[str]:
    """The keychain command that prints a team's key: the `keychain_check` of the config with that key's item in place of the
    first one, so the account, the keychain and the item prefix are the config's own (`-w` is there: `load_service`)."""
    words = shlex.split(svc.keychain_check)
    if "find-generic-password" not in words or "-s" not in words[:-1]:
        raise Refused(f"{svc.config}: [service] keychain_check must be a `security find-generic-password ... -s ITEM -w KEYCHAIN` line, "
                      f"so that the key {variable} has a lookup of its own")
    at = words.index("-s") + 1
    return [*words[:at], f"{words[at].rsplit('.', 1)[0]}.{key_item(variable)}", *words[at + 1:]]


def key_args(svc: Service) -> list[str]:
    """`service.py`'s `--env-from` options: one lookup for each team key besides LINEAR_API_KEY."""
    return [arg for var in svc.keys for arg in ("--env-from", f"{var}={shlex.join(key_lookup(svc, var))}")]


def with_keys(svc: Service, folder: Path, command: list[str], log: Path | None = None) -> list[str]:
    """`command`, run by `service.py` with each extra team key in its environment. It stands inside `agent_user.py run`, which
    clears the key variables of its caller. With no extra key the command is as it was."""
    if not svc.keys:
        return command
    return ["/usr/bin/python3", "-I", str(folder / "service.py"), *(["--log", str(log)] if log else []), *key_args(svc), "--", *command]


def load_service(config: Path) -> Service:
    try:
        data = tomllib.loads(config.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise Refused(f"cannot read {config} ({e}): copy orchestrator/orchestrator.example.toml there with sudo, then edit it") from None
    table = data.get("service") or {}
    missing = [k for k in ("user", "install_dir", "python", "label", "log_file", "keychain_check") if not table.get(k)]
    if missing:
        raise Refused(f"{config}: [service] needs {', '.join(missing)} (orchestrator.example.toml has them)")
    words = shlex.split(str(table["keychain_check"]))
    if "find-generic-password" in words and "-w" not in words:
        raise Refused(f"{config}: [service] keychain_check needs -w: without it a locked keychain passes the check, and the "
                      "service restarts every 30 s (orchestrator.example.toml has the line)")
    keys = tuple(dict.fromkeys(var for _, var in team_keys(data, config) if var != DEFAULT_KEY_VARIABLE))
    return Service(str(table["user"]), Path(table["install_dir"]), Path(table["python"]), str(table["label"]),
                   Path(table["log_file"]), str(table["keychain_check"]), config, keys, project_users(data, config))


Run = Callable[..., "subprocess.CompletedProcess[str]"]


def run_cmd(cmd: Sequence[str], stdin: str | bytes | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    data = stdin.encode() if isinstance(stdin, str) else stdin
    done = subprocess.run(list(cmd), input=data, cwd=cwd, capture_output=True, check=False)
    return subprocess.CompletedProcess(done.args, done.returncode, done.stdout.decode(errors="replace"),
                                       done.stderr.decode(errors="replace"))


def must(run: Run, cmd: Sequence[str], **kw: Any) -> str:
    done = run(cmd, **kw)
    if done.returncode != 0:
        raise Refused(f"{shlex.join(cmd)} failed: {(done.stderr or done.stdout).strip()[:300]}")
    return str(done.stdout)


# The orchestrator's Python runs with -I: without it, Python adds the agents' user's own site-packages folder to sys.path and
# runs each .pth file there at start, so the agents could put code into the service, which holds the Linear key.
# -I also ignores PYTHONUNBUFFERED, so -u keeps the log lines prompt.


def as_agent(svc: Service, folder: Path, *args: str, config: Path | None = None) -> list[str]:
    """A command as the agents' user, in `folder`, through that folder's `agent_user.py run` (its Linear key, its own gh shim),
    on `config` (the installed one by default)."""
    inner = [str(svc.python), "-I", "-u", str(folder / "orchestrator.py"), "--config", str(config or svc.config), *args]
    if args != ("--preflight",):  # the offline preflight needs no Linear key or installed local review settings
        inner = ["/usr/bin/python3", "-I", str(folder / "agent_user.py"), "run", "--", *with_keys(svc, folder, inner)]
    return ["sudo", "-u", svc.user, "-H", "/bin/sh", "-c", f"cd {shlex.quote(str(folder))} && exec {shlex.join(inner)}"]


# sudo keeps the caller's working folder, and the agents' user cannot read the owner's checkout: the shell would print
# "getcwd: cannot access parent directories" for each command. Each command of that user starts in `/`.
AGENT_CWD = Path("/")


def require_owner(svc: Service, run: Run) -> None:
    """Refuse to run as the agents' user or as a user that is not an admin: sudo then fails with a message that hides the cause."""
    who = run(["id", "-un"], cwd=AGENT_CWD).stdout.strip()
    groups = run(["id", "-Gn"], cwd=AGENT_CWD).stdout.split()
    if who == svc.user or "admin" not in groups:
        raise Refused(f"run this from your own admin account (not '{who or 'unknown'}'), in a clean checkout of main")


# The rotation: each file rotates at LOG_ROTATE_KB, and LOG_KEEP old files stay beside it, so the log takes at most
# (LOG_KEEP + 1) x LOG_ROTATE_KB plus what it grows between two newsyslog runs (launchd runs newsyslog once an hour).
LOG_ROTATE_KB = 1024
LOG_KEEP = 5


def rotation_for(svc: Service) -> str:
    """The newsyslog entry: owner the agents' user (it appends to the new file), mode 640, rotated by size, the flag N."""
    return "".join(f"{path}\t{svc.user}:\t640\t{LOG_KEEP}\t{LOG_ROTATE_KB}\t*\tN\n" for path in (svc.log_file, svc.crash_log))


# The waiting line's reason: only an unlock in the domain's own session reaches the service, and a pane has a
# session of its own after a restart.
WHY = ("the agent's keychain is locked in the service's session: from your account, run `owlcode unlock` "
       "(an unlock in a pane on the machine {user} does not reach the service)")


def plist_for(svc: Service) -> bytes:
    """The LaunchAgent: root's file, loaded into the agents' user domain (it runs as that user), in the Background session
    type, restarted whenever it ends. service.py and orchestrator.py append their lines to the log file themselves
    (`--log`); launchd's own output goes to the crash log."""
    service = ["/usr/bin/python3", "-I", str(svc.install_dir / "service.py"), "--period", "60", "--user", svc.user,
               "--why", WHY.format(user=svc.user),
               "--log", str(svc.log_file), "--check", svc.keychain_check, "--",
               "/usr/bin/python3", "-I", str(svc.install_dir / "agent_user.py"), "run", "--",
               *with_keys(svc, svc.install_dir, [str(svc.python), "-I", "-u", str(svc.install_dir / "orchestrator.py"),
                                                 "--config", str(svc.config), "--log", str(svc.log_file)], svc.log_file)]
    return plistlib.dumps({
        "Label": svc.label, "LimitLoadToSessionType": "Background", "ProgramArguments": service,
        "WorkingDirectory": str(svc.install_dir), "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 30, "ProcessType": "Background",
        "StandardOutPath": str(svc.crash_log), "StandardErrorPath": str(svc.crash_log),
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1", "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
    })


TABLE_LINE = re.compile(r"^\[\[?([^\[\]]+)\]\]?\s*(#.*)?$")  # `[[product]]` ends a table too: a key added to the table before it must not land in it
KEY_LINE = re.compile(r"^([A-Za-z0-9_-]+)\s*=")


def example_blocks(text: str) -> dict[str, dict[str, list[str]]]:
    """The example's keys by table ("" is the top of the file): each key's own line and the comment lines under it that
    start with a space (the example's habit: a comment goes on the key's line and goes on below it, indented)."""
    tables: dict[str, dict[str, list[str]]] = {"": {}}
    table, last = "", None
    for line in text.splitlines():
        if header := TABLE_LINE.match(line):
            table, last = header.group(1).strip(), None
            tables.setdefault(table, {})
        elif key := KEY_LINE.match(line):
            last = tables[table].setdefault(key.group(1), [])
            last.append(line)
        elif last is not None and re.match(r"^\s+#", line):
            last.append(line)
        else:
            last = None
    return tables


def _section(data: dict[str, Any], table: str) -> dict[str, Any]:
    """The table's keys; a dotted name (`[profiles.claude]`) is a table inside a table."""
    found: Any = data
    for part in table.split(".") if table else ():
        found = found.get(part) if isinstance(found, dict) else None
    return found if isinstance(found, dict) else {}


def missing_keys(example: str, installed: str) -> dict[str, dict[str, list[str]]]:
    """The keys that the example has and the installed config lacks, by table, with their lines. A key that the installed
    config has is never in it, whatever its value."""
    try:
        have = tomllib.loads(installed)
    except tomllib.TOMLDecodeError as e:
        raise Refused(f"cannot read the installed config as TOML ({e})") from None
    found: dict[str, dict[str, list[str]]] = {}
    for table, keys in example_blocks(example).items():
        for key, lines in keys.items():
            if key not in _section(have, table):
                found.setdefault(table, {})[key] = lines
    return found


def different_values(example: str, installed: str) -> list[str]:
    """One line for each key that both files have with another value: printed for the owner, never changed here."""
    want, have = tomllib.loads(example), tomllib.loads(installed)
    rows: list[str] = []
    for table, keys in example_blocks(example).items():
        a, b = _section(want, table), _section(have, table)
        rows += [f"  {f'[{table}] ' if table else ''}{key}: installed {b[key]!r}, example {a[key]!r}"
                 for key in keys if key in b and a[key] != b[key]]
    return rows


def add_keys(installed: str, missing: dict[str, dict[str, list[str]]]) -> str:
    """The installed text with the missing keys added at the end of their table (a new table at the end of the file).
    Every other line stays as it is."""
    lines = installed.splitlines()
    for table, keys in missing.items():
        new = [line for block in keys.values() for line in block]
        heads = [i for i, line in enumerate(lines) if TABLE_LINE.match(line)]
        if table == "":
            at = heads[0] if heads else len(lines)
        else:
            start = next((i for i in heads if (m := TABLE_LINE.match(lines[i])) and m.group(1).strip() == table), None)
            if start is None:
                lines += ["", f"[{table}]"]
                at = len(lines)
            else:
                at = next((i for i in heads if i > start), len(lines))
        while at > 0 and not lines[at - 1].strip():  # after the section's last line, before the blank lines that follow it
            at -= 1
        lines[at:at] = new
        if table == "" and heads:
            lines.insert(at + len(new), "")
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class Retired:
    """A key (or, with an empty `key`, a whole table) that the orchestrator refuses as retired: the install removes it."""
    table: str  # "" is the top of the file; a dotted name is a table inside a table
    key: str
    ref: str


# The retired keys. A change that retires a config key adds it here, and the code that refuses it
# (`load_config` of orchestrator.py) names `owlcode install` as the fix: the install removes the key on the
# same yes that adds the new keys. The new keys that replace a retired one come from the example (`missing_keys`).
RETIRED = (
    Retired("", "run_command", "the harness and the host build the start command"),
    Retired("github", "conflict_marker", "conflict_comment and conflict_author replace it"),
    *(Retired("run_settings", cell, "the cells moved to the [profiles.NAME] tables") for cell in CELLS),
    Retired("profiles.ollama", "", "[profiles.pi-ollama] replaces it"),  # the new table comes from the example
    Retired("profiles.opencode-ollama", "", "OpenCode was dropped"),  # an older install holds both tables
    Retired("harnesses.opencode", "", "OpenCode was dropped"),
)
# A value that a retired name left behind: (key, old value, new value, the reason). The first one also needs session_host = "paseo":
# pi runs only in Paseo (README.md).
RENAMED = (("inference_profile", "ollama", "pi-ollama", "the profile ollama is now pi-ollama"),)


def _drop_key(lines: list[str], table: str, key: str) -> tuple[list[str], list[str]]:
    """The lines without the key's own line and the indented comment lines under it; and the lines that went."""
    current = ""
    for i, line in enumerate(lines):
        if header := TABLE_LINE.match(line):
            current = header.group(1).strip()
        elif current == table and (found := KEY_LINE.match(line)) and found.group(1) == key:
            end = i + 1
            while end < len(lines) and re.match(r"^\s+#", lines[end]):
                end += 1
            return lines[:i] + lines[end:], lines[i:end]
    return lines, []


def _drop_table(lines: list[str], name: str) -> tuple[list[str], list[str]]:
    """The lines without the table and the tables inside it; and the lines that went. The blank lines and the comment lines
    at the end of the table's block stay: the example's habit puts the comment of a table above its header."""
    kept: list[str] = []
    gone: list[str] = []
    held: list[str] = []  # the blank and unindented comment lines since the last line of the dropped table
    dropping = False

    def release() -> None:
        while held and not held[0].strip() and kept and not kept[-1].strip():  # one blank line between two tables, not two
            held.pop(0)
        kept.extend(held)
        held.clear()

    for line in lines:
        if header := TABLE_LINE.match(line):
            release()
            found = header.group(1).strip()
            dropping = found == name or found.startswith(name + ".")
        if not dropping:
            kept.append(line)
        elif not line.strip() or line.startswith("#"):
            held.append(line)
        else:
            gone += held + [line]
            held.clear()
    release()
    return kept, gone


def _note(lines: list[str], mark: str, note: str) -> list[str]:
    """The lines with `mark` before each one and the note after the first."""
    return [f"{mark}{line}" + (f"{'':<{max(2, 32 - len(line))}}({note})" if i == 0 else "") for i, line in enumerate(lines)]


def retire_keys(installed: str) -> tuple[str, list[str], int]:
    """The installed text without the retired keys, the lines that show each edit (`-` and `+`, as a diff does), and
    the number of edits. A retired `inference_profile = "ollama"` becomes "pi-ollama", and `session_host` "paseo". Every other
    line stays. The result is parsed again, and only these edits differ: else nothing is written (a value on many lines)."""
    try:
        data = tomllib.loads(installed)
    except tomllib.TOMLDecodeError as e:
        raise Refused(f"cannot read the installed config as TOML ({e})") from None
    lines = installed.splitlines()
    shown: list[str] = []
    names: list[str] = []  # each retired key that the config holds, with its reason, for the refusal's message
    count = 0
    for item in RETIRED:
        parent = _section(data, item.table) if item.key else _section(data, item.table.rpartition(".")[0])
        name = item.key or item.table.rpartition(".")[2]
        if name not in parent:
            continue
        lines, gone = _drop_key(lines, item.table, item.key) if item.key else _drop_table(lines, item.table)
        del parent[name]
        names.append(f"[{item.table}] ({item.ref})" if not item.key else f"{f'[{item.table}] ' if item.table else ''}{item.key} ({item.ref})")
        shown += _note(gone, "-", f"retired: {item.ref}")
        count += 1
    for key, old, new, ref in RENAMED:
        if data.get(key) != old:
            continue
        names.append(f"{key} = {old!r} ({ref})")
        edits = [(key, f'"{new}"', ref)] + ([("session_host", '"paseo"', ref)] if data.get("session_host") != "paseo" else [])
        for edit, value, why in edits:
            pattern = re.compile(rf"^({edit}[ \t]*=[ \t]*)({VALUE})")
            head = next((i for i, line in enumerate(lines) if pattern.match(line)), None)
            if head is None or any(TABLE_LINE.match(line) for line in lines[:head]):
                continue  # a key that is not at the top of the file: the dry round names it
            before = lines[head]
            lines[head] = pattern.sub(lambda m, v=value: str(m.group(1)) + v, before, count=1)  # type: ignore[misc]
            data[edit] = tomllib.loads(f"v = {value}")["v"]
            shown += _note([before], "-", f"renamed: {why}") + [f"+{lines[head]}"]
        count += 1
    text = "\n".join(lines) + "\n" if count else installed
    try:
        parsed = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        parsed = None
    if parsed != data:
        raise Refused("cannot remove the retired keys safely (a value on many lines): nothing was written. Edit these "
                      f"retired keys out of the config by hand, then run the install again: {', '.join(names)}")
    return text, shown, count


def plan_config(svc: Service, checkout: Path, out: Callable[[str], None]) -> tuple[str, str]:
    """Print the changes that the install makes to the installed config, as one diff: each retired
    key that goes (`retire_keys`), then each key that the example has and the config lacks. Returns the config text with the
    changes and their summary ("" when nothing changes, and the text is the installed one). It writes nothing: the install
    writes the text after its yes. A value that is not retired is never changed."""
    try:
        example = (checkout / "orchestrator" / "orchestrator.example.toml").read_text()
        installed = svc.config.read_text()
    except OSError as e:
        raise Refused(f"cannot read a config file ({e})") from None
    base, shown, retired = retire_keys(installed)
    differ = different_values(example, base)
    if differ:
        out("Values that differ from the example (kept: change one by hand when you want to):\n" + "\n".join(differ))
    missing = missing_keys(example, base)
    count = sum(len(keys) for keys in missing.values())
    if not retired and not count:
        return installed, ""
    for table, keys in missing.items():
        where = f"[{table}]" if table else "the top of the file"
        for block in keys.values():
            shown += _note(block[:1], "+", f"new in {where}") + [f"+{line}" for line in block[1:]]
    out(f"the config's changes ({svc.config}):\n" + "\n".join(shown))
    text = add_keys(base, missing) if count else base
    try:  # the text goes over the only copy of root's file: it must parse, and hold every key (a multi-line value breaks it)
        left = missing_keys(example, text)
    except Refused:
        left = missing
    if left:
        names = ", ".join(f"[{table}] {key}" if table else key for table, keys in missing.items() for key in keys)
        raise Refused(f"cannot add the keys safely (a value on many lines, or a table written inline): nothing was written. "
                      f"Add these keys to {svc.config} by hand, each with its whole value as "
                      f"orchestrator/orchestrator.example.toml has it: {names}")
    parts = ([f"change {retired} retired key{'s' if retired != 1 else ''}"] if retired else []) + \
            ([f"add {count} key{'s' if count != 1 else ''}"] if count else [])
    return text, " and ".join(parts)


def cell_profiles(name: str, table: dict[str, object], tables: dict[str, object]) -> list[str]:
    """The profile `name` first, then each other profile that its cells name (a cell is [model, effort, PROFILE], or
    [model, PROFILE] when the harness takes no effort level), in the order of the cells."""
    found = [name]
    for cell in CELLS:
        value = table.get(cell)
        other = name
        if isinstance(value, list) and len(value) == 3:
            other = value[2]
        elif isinstance(value, list) and len(value) == 2 and isinstance(value[1], str) and value[1] in tables:
            other = value[1]
        if isinstance(other, str) and other not in found:
            if other not in tables:
                raise Refused(f"[profiles.{name}] {cell} names {other!r}, which has no [profiles.{other}] table")
            found.append(other)
    return found


def review_settings(config_text: str) -> str:
    """The local review's model and effort level of each `[profiles.NAME]` table as JSON, for the installed `agent_user.py`,
    with the profiles that its cells run on (`cell_profiles`). A profile that names no review stops this
    here, before anything changes: the key-adding of `plan_config` reaches only the profiles that the example holds, and the
    script would refuse the file it does not match."""
    try:
        data = tomllib.loads(config_text)
    except tomllib.TOMLDecodeError as e:
        raise Refused(f"the config does not parse ({e})") from None
    tables = data.get("profiles")
    if not isinstance(tables, dict) or not tables:
        raise Refused("the config names no [profiles.NAME] table: each inference profile needs one (orchestrator.example.toml)")
    read: dict[str, dict[str, Any]] = {}
    for name, table in tables.items():
        where = f"[profiles.{name}]"
        if not isinstance(table, dict):
            raise Refused(f"{where} is not a table")
        values: dict[str, Any] = {}
        for key in ("review_model", "review_effort"):
            value = table.get(key)
            if not isinstance(value, str) or not REVIEW_NAME.fullmatch(value):
                raise Refused(f"{where} {key} is missing or is no name ({value!r}): the local review takes one model and one "
                              "effort level for each profile, as orchestrator.example.toml has them")
            values[key.split("_", 1)[1]] = value
        values["cells"] = cell_profiles(name, table, tables)
        read[name] = values
    return json.dumps(dict(sorted(read.items())), indent=2, sort_keys=True) + "\n"


def profile_setting(config_text: str) -> str:
    """The config's `inference_profile` as JSON, for the installed `agent_user.py paseo-daemon`. A profile with no
    `[profiles.NAME]` table stops this here, as `review_settings` stops a table with no review."""
    data = tomllib.loads(config_text)
    name = data.get("inference_profile")
    if not isinstance(name, str) or name not in (data.get("profiles") or {}):
        raise Refused(f"inference_profile {name!r} has no [profiles.NAME] table (orchestrator.example.toml)")
    return json.dumps({"inference_profile": name}) + "\n"


def side_by_side(left: str, right: str, width: int = 60) -> str:
    a, b = left.splitlines() or ["(nothing)"], right.splitlines() or ["(nothing)"]
    rows = [f"{'installed':<{width}} | new", f"{'-' * width}-+-{'-' * width}"]
    for i in range(max(len(a), len(b))):
        x, y = (a[i] if i < len(a) else ""), (b[i] if i < len(b) else "")
        rows.append(f"{x[:width]:<{width}} {'|' if x == y else '*'} {y[:width]}")
    return "\n".join(rows)


def uid_of(svc: Service, run: Run) -> str:
    """The agents' user's uid: its launchd domain is `user/UID`."""
    uid = must(run, ["id", "-u", svc.user]).strip()
    if not uid.isdigit():
        raise Refused(f"`id -u {svc.user}` gave no uid ({uid[:100]!r})")
    return uid


def stage_permission_detail(stage: Path, run: Run, done: subprocess.CompletedProcess[str]) -> str:
    """Report the staging folder mode when a staged command reports a permission error."""
    if "permission denied" not in f"{done.stdout}\n{done.stderr}".lower():
        return ""
    mode = run(["sudo", "stat", "-f", "%OLp", str(stage)])
    value = mode.stdout.strip() if mode.returncode == 0 else "unavailable"
    return f"\nStaging folder: {stage} (mode {value})"


def install(svc: Service, checkout: Path, run: Run, ask: Callable[[str], str], out: Callable[[str], None]) -> int:
    require_owner(svc, run)
    must(run, ["git", "-C", str(checkout), "fetch", "-q", "origin", "main"])
    head = must(run, ["git", "-C", str(checkout), "rev-parse", "HEAD"]).strip()
    if head != must(run, ["git", "-C", str(checkout), "rev-parse", "origin/main"]).strip():
        raise Refused("the checkout is not origin/main: pull main first (the service runs what main holds)")
    if must(run, ["git", "-C", str(checkout), "status", "--porcelain", "--", *FILES]).strip():
        raise Refused("the checkout has changes in the installed files: commit or drop them first")
    done = run(["cat", str(svc.install_dir / "COMMIT")])
    installed = done.stdout.strip() if done.returncode == 0 else ""
    # (0) the keys of the example that the installed config lacks: the text is written only after the yes
    config_text, changes = plan_config(svc, checkout, out)
    review = review_settings(config_text)  # a profile without its review's model stops here, before anything changes
    profile = profile_setting(config_text)
    # (1) what changes
    known = bool(installed) and run(["git", "-C", str(checkout), "cat-file", "-e", f"{installed}^{{commit}}"]).returncode == 0
    if installed and not known:
        # the installed copy came from another repository's checkout: git has no range to show
        out(f"The installed version {installed[:12]} is not a commit of this checkout: the diff compares the installed "
            "files with this checkout's.")
        for source, target in FILES.items():
            have = run(["cat", str(svc.install_dir / target)])
            now = (checkout / source).read_text()
            diff = "".join(difflib.unified_diff(have.stdout.splitlines(True) if have.returncode == 0 else [], now.splitlines(True),
                                                f"installed/{target}", f"checkout/{source}"))
            out(diff.rstrip() or f"{target}: no change")
    else:
        span = f"{installed}..{head}" if installed else head
        log = must(run, ["git", "-C", str(checkout), "log", "--oneline", span, "--", *FILES]) if installed else "(first install)\n"
        out(f"Commits since the installed version ({installed[:12] or 'none'}):\n{log.rstrip() or '(none)'}")
    # (2) the Python
    checked = run(["sudo", "-u", svc.user, "-H", str(svc.python), "-I", "-c", PYTHON_CHECK], cwd=AGENT_CWD)
    if checked.returncode != 0:
        lines = checked.stderr.strip().splitlines()
        raise Refused(f"the Python check failed ({lines[-1] if lines else 'no message'}). {svc.python} must be Python 3.11 or "
                      "newer with root certificates (AGENTS.md 'Traps': run 'Install Certificates.command' of the python.org "
                      "build once)")
    domain = f"user/{uid_of(svc, run)}"  # the service's launchd domain, read before anything changes
    # (3) stage the new files, then a dry round of each copy
    must(run, ["sudo", "rm", "-rf", str(svc.stage)])
    # Set the mode of any new parent folders too. Leave existing parents as they are.
    must(run, ["sudo", "/bin/sh", "-c", 'umask 022; exec /bin/mkdir -p "$@"', "mkdir",
               str(svc.stage / ".github"), str(svc.stage / "bin")])
    for source, target in FILES.items():
        must(run, ["sudo", "install", "-o", "root", "-g", "wheel", "-m", "644", str(checkout / source), str(svc.stage / target)])
    must(run, ["sudo", "tee", str(svc.stage / "bin" / "gh")], stdin=GH_SHIM)
    must(run, ["sudo", "tee", str(svc.stage / REVIEW_FILE)], stdin=review)
    must(run, ["sudo", "tee", str(svc.stage / PROFILE_FILE)], stdin=profile)
    must(run, ["sudo", "tee", str(svc.stage / "COMMIT")], stdin=head + "\n")
    # the installed code reads the installed config (it calls a new key unknown); the new code reads a staged copy with the keys
    staged_config = svc.stage / "orchestrator.toml" if changes else None
    if staged_config:
        must(run, ["sudo", "tee", str(staged_config)], stdin=config_text)
    must(run, ["sudo", "chown", "-R", "root:wheel", str(svc.stage)])
    must(run, ["sudo", "chmod", "-R", "u=rwX,go=rX", str(svc.stage)])
    must(run, ["sudo", "chmod", "755", str(svc.stage / "bin" / "gh")])
    preflight = run(as_agent(svc, svc.stage, "--preflight", config=staged_config), cwd=AGENT_CWD)
    if preflight.returncode != 0:
        detail = stage_permission_detail(svc.stage, run, preflight)
        raise Refused(f"the staged setup preflight failed: nothing was installed\n{(preflight.stderr or preflight.stdout).strip()}{detail}")
    old = run(as_agent(svc, svc.install_dir, "--once", "--dry-run"), cwd=AGENT_CWD) if installed else None
    new = run(as_agent(svc, svc.stage, "--once", "--dry-run", config=staged_config), cwd=AGENT_CWD)
    out("One dry round of each (`*`: the lines differ):")
    out(side_by_side(old.stdout + old.stderr if old else "(not installed)", new.stdout + new.stderr))
    if new.returncode != 0:
        detail = stage_permission_detail(svc.stage, run, new)
        raise Refused(f"the new code's dry round failed: nothing was installed{detail}")
    # (4) the owner's yes
    extra = f", {changes} in {svc.config}" if changes else ""
    if ask(f"Install {head[:12]}{extra} and restart the service? [y/N] ").strip().lower() not in ("y", "yes"):
        out("Nothing installed.")
        must(run, ["sudo", "rm", "-rf", str(svc.stage)])
        return 1
    # (5) the new keys, swap, load
    if changes:
        must(run, ["sudo", "tee", str(svc.config)], stdin=config_text)
        out(f"Changed {svc.config}: {changes}.")
    must(run, ["sudo", "rm", "-rf", str(svc.old)])
    if installed:
        must(run, ["sudo", "mv", str(svc.install_dir), str(svc.old)])
    must(run, ["sudo", "mv", str(svc.stage), str(svc.install_dir)])
    if changes:  # the staged config copy was for the dry round only
        must(run, ["sudo", "rm", "-f", str(svc.install_dir / "orchestrator.toml")])
    must(run, ["sudo", "tee", str(svc.rotation)], stdin=rotation_for(svc))
    must(run, ["sudo", "chown", "root:wheel", str(svc.rotation)])
    must(run, ["sudo", "chmod", "644", str(svc.rotation)])
    parsed = run(["sudo", "newsyslog", "-n", "-f", str(svc.rotation)])  # reads the entry without a rotation
    if parsed.returncode != 0:  # a warning, not a stop: the new copy is in place already, and the log works without rotation
        out(f"WARN newsyslog cannot read {svc.rotation}: {(parsed.stderr or parsed.stdout).strip()[:300]}")
    must(run, ["sudo", "tee", str(svc.plist)], stdin=plist_for(svc))
    must(run, ["sudo", "chown", "root:wheel", str(svc.plist)])
    must(run, ["sudo", "chmod", "644", str(svc.plist)])
    # The log is in the agents' home, which they can write: one root step opens it, checks that descriptor and changes its
    # owner (a check and a chown by path, as two commands, leave a gap for a swap to a hard link).
    answer = run(["sudo", "/usr/bin/python3", "-I", "-c", GIVE_FILE, str(svc.log_file), svc.user])
    result = answer.stdout.strip()
    if answer.returncode != 0:
        raise Refused(f"cannot give {svc.log_file} to {svc.user}: {(answer.stderr or result).strip()[:300]}")
    if result.startswith("refused"):
        out(f"WARN {svc.log_file}: its owner was not changed ({result})")
    # The LaunchDaemon of an older install goes first, so two orchestrators never run at once
    run(["sudo", "launchctl", "bootout", f"system/{svc.label}"])  # not loaded: an error that changes nothing
    must(run, ["sudo", "rm", "-f", str(svc.daemon_plist)])
    run(["sudo", "launchctl", "bootout", f"{domain}/{svc.label}"])  # not loaded yet on a first install: no error
    loaded = run(["sudo", "launchctl", "bootstrap", domain, str(svc.plist)])
    # launchd also loads the plist into the owner's domain at login and root's at boot; disabled there, it stays out.
    # this runs also when the load failed, so a first install with no SSH login of the agents' user is safe too.
    others, _ = other_domains(run)
    for other_uid, _whose in others:
        if f"user/{other_uid}" == domain:
            continue
        must(run, ["sudo", "launchctl", "disable", f"user/{other_uid}/{svc.label}"])
        run(["sudo", "launchctl", "bootout", f"user/{other_uid}/{svc.label}"])  # not loaded there: an error that changes nothing
    if loaded.returncode != 0:
        raise Refused(f"the copy {head[:12]} is installed, but launchctl cannot load {svc.plist} into {domain} "
                      f"({(loaded.stderr or loaded.stdout).strip()[:200]}). The job is disabled in the other domains. "
                      f"The domain exists only after an SSH login of "
                      f"{svc.user} after a restart: open a pane on the machine {svc.user}, then run "
                      f"`sudo launchctl bootstrap {domain} {svc.plist}`")
    out(f"Installed {head[:12]}; the service {svc.label} runs as {svc.user} in {domain}. Its log: {svc.log_file}. What a start that "
        f"stops prints (a config error, a traceback): {svc.crash_log}. Both rotate at {LOG_ROTATE_KB} KB ({svc.rotation})")
    return 0


# What `check` reads of the install itself: a find that prints each file that the agents' user could change.
# BSD find on macOS: `-perm -020` is a group-writable file, `-perm -002` an other-writable one.
NOT_ROOTS = ["(", "!", "-user", "root", "-o", "-perm", "-020", "-o", "-perm", "-002", ")", "-print"]


def _line(ok: bool, text: str) -> tuple[bool, str]:
    return ok, f"{'ok  ' if ok else 'FAIL'} {text}"


def other_domains(run: Run) -> tuple[list[tuple[str, str]], str | None]:
    """The launchd domains where the job must not run: the owner's own `user/UID` (the account that runs
    this script) and root's `user/0`. launchd loads a Background LaunchAgent of /Library/LaunchAgents into each at login or
    at boot. Returns the domains as `(uid, whose)` and, when `id -u` gives no uid, the reason, so the owner's is left out."""
    mine = run(["id", "-u"])
    uid = mine.stdout.strip()
    if mine.returncode != 0 or not uid.isdigit():
        return [("0", "root")], f"`id -u` gave no uid ({mine.stderr.strip()[:100] or uid[:100] or 'nothing'})"
    return ([(uid, "the owner")] if uid != "0" else []) + [("0", "root")], None


def disabled_in(run: Run, uid: str, label: str) -> bool:
    """launchd's override database says the label is disabled in `user/UID` (`launchctl print-disabled`: `"LABEL" =>
    disabled`, or `=> true` on an older macOS). A disabled job is not loaded there, also after a restart."""
    shown = run(["sudo", "launchctl", "print-disabled", f"user/{uid}"])
    if shown.returncode != 0:
        return False
    found = re.search(rf'^\s*"{re.escape(label)}"\s*=>\s*(\w+)\s*$', shown.stdout, re.MULTILINE)
    return found is not None and found.group(1) in ("disabled", "true")


def own_properties(svc: Service, run: Run) -> list[tuple[bool, str]]:
    """The install's own properties, each one `(passed, line)`: who runs the service and in which launchd domain, that the job is not in the owner's own domain or root's
    `user/0`, who may
    change its files, that launchd restarts it, that its gh shim is root's and found first, and that its keychain check holds
    `-w`. A command that fails is a FAIL line with the reason, never an exception."""
    rows: list[tuple[bool, str]] = []
    # the process: the job in the agents' user domain, the pid that launchd reports, and that pid's user
    uid = run(["id", "-u", svc.user])
    domain = f"user/{uid.stdout.strip()}" if uid.returncode == 0 and uid.stdout.strip().isdigit() else ""
    state = run(["sudo", "launchctl", "print", f"{domain}/{svc.label}"]) if domain else None
    loaded = state.stdout if state is not None and state.returncode == 0 else ""
    pid = re.search(r"^\s*pid = (\d+)\s*$", loaded, re.MULTILINE)
    if not pid:
        where = f"launchctl print {domain}/{svc.label}" if domain else f"`id -u {svc.user}` gave no uid"
        rows.append(_line(False, f"the process: {svc.label} is not loaded in the domain of {svc.user} or has no pid ({where})"))
    else:
        ps = run(["ps", "-o", "user=", "-p", pid.group(1)])
        user = ps.stdout.strip()
        rows.append(_line(ps.returncode == 0 and user == svc.user,
                          f"the process {pid.group(1)} runs as {user or ps.stderr.strip()[:100] or 'nobody'} (want {svc.user}, not root and not the owner)"))
    # the job's plist: root's file. The agents' user controls its own domain, so it could load a plist of its own in its place
    path = re.search(r"^\s*path = (.+?)\s*$", loaded, re.MULTILINE)
    rows.append(_line(path is not None and path.group(1) == str(svc.plist),
                      f"the job is loaded from {svc.plist} (found: {path.group(1) if path else 'nothing'})"))
    # the owner's own domain: a Background LaunchAgent in /Library/LaunchAgents may load into every user's domain at
    # login. A job here idles (service.py --user), but it must not be there: unload it with `launchctl bootout`
    # launchd also loads it into root's domain `user/0` at boot, before any login (measured 2026-11-19), where it
    # idles as root
    # `launchctl disable` keeps launchd from loading it there after a restart; the install runs it
    others, no_uid = other_domains(run)
    if no_uid:
        rows.append(_line(False, f"the owner's domain: {no_uid}"))
    for other_uid, whose in others:
        domain_label = f"user/{other_uid}/{svc.label}"
        there = run(["sudo", "launchctl", "print", domain_label]).returncode == 0
        off = disabled_in(run, other_uid, svc.label)
        fix = ([] if off else [f"`sudo launchctl disable {domain_label}`"]) + ([f"`sudo launchctl bootout {domain_label}`"] if there else [])
        rows.append(_line(off and not there, f"{svc.label} is disabled and not loaded in the domain of {whose} (user/{other_uid})"
                          if off and not there else
                          f"{svc.label} is {'loaded' if there else 'not disabled'} in the domain of {whose} (user/{other_uid}): "
                          f"{'it idles, but ' if there else 'launchd loads it there after a restart; '}run {' then '.join(fix)}"))
    # no LaunchDaemon of an older install: the system domain's session is not the panes', so no unlock reaches it, and the
    # pane's lookup passes while that service waits
    old = run(["sudo", "launchctl", "print", f"system/{svc.label}"]).returncode == 0
    old_file = run(["test", "-e", str(svc.daemon_plist)]).returncode == 0
    rows.append(_line(not old and not old_file, f"no LaunchDaemon of an older install (system/{svc.label}), whose session no "
                      "unlock in a pane reaches" if not old and not old_file else
                      f"the LaunchDaemon of an older install is {'loaded' if old else 'there'} (system/{svc.label}, "
                      f"{svc.daemon_plist}): no unlock in a pane reaches its session; run `owlcode install`"))
    # the files: root's, and not writable by group or others
    for what, args in (("the installed copy", [str(svc.install_dir)]), ("the LaunchAgent plist", [str(svc.plist), "-maxdepth", "0"])):
        found = run(["find", args[0], *args[1:], *NOT_ROOTS])
        names = found.stdout.split()
        if found.returncode != 0:
            rows.append(_line(False, f"{what}: cannot read {args[0]} ({found.stderr.strip()[:200]})"))
        else:
            rows.append(_line(not names, f"{what} is root's and not writable by group or others"
                              + (f" (not so: {', '.join(names[:5])})" if names else "")))
    # the plist's KeepAlive
    plist = run(["cat", str(svc.plist)])
    prog: list[str] = []
    try:
        data = plistlib.loads(plist.stdout.encode()) if plist.returncode == 0 else {}
        keep = data.get("KeepAlive")
        prog = [str(a) for a in data.get("ProgramArguments", [])]
    except Exception as e:  # plistlib raises several types for a bad file; the line says which
        keep, plist.stderr = None, str(e)
    rows.append(_line(keep is True, "the plist has KeepAlive, so a kill restarts the service" if keep is True
                      else f"the plist has no KeepAlive true ({plist.stderr.strip()[:200] or keep!r})"))
    # the gh shim: root's, mode 755, and first on the service's PATH
    shim = svc.install_dir / "bin" / "gh"
    meta = run(["stat", "-f", "%Su %Lp", str(shim)])
    rows.append(_line(meta.returncode == 0 and meta.stdout.strip() == "root 755",
                      f"{shim} is root's, mode 755 (found: {meta.stdout.strip() or meta.stderr.strip()[:100] or 'nothing'})"))
    inner = shlex.join(["/usr/bin/python3", "-I", str(svc.install_dir / "agent_user.py"), "run", "--", "/usr/bin/which", "gh"])
    first = run(["sudo", "-u", svc.user, "-H", "/bin/sh", "-c", f"cd {shlex.quote(str(svc.install_dir))} && exec {inner}"],
                cwd=AGENT_CWD)
    rows.append(_line(first.returncode == 0 and first.stdout.strip() == str(shim),
                      f"the service's PATH finds {shim} before any other gh (found: {first.stdout.strip() or first.stderr.strip()[:200] or 'nothing'})"))
    # the check that the running service waits on is the plist's `--check` argument, not the config's line
    waits = shlex.split(prog[prog.index("--check") + 1]) if "--check" in prog[:-1] else []
    rows.append(_line("-w" in waits or ("find-generic-password" not in waits and bool(waits)),
                      f"the plist's --check holds -w, so a locked keychain waits (found: {' '.join(waits) or 'no --check'})"))
    # a team with a key of its own in a second Linear workspace: the plist has a lookup for its variable
    for var in svc.keys:
        rows.append(_line(any(a.startswith(f"{var}=") for a in prog) and "--env-from" in prog,
                          (f"the plist gives the service {var} (the keychain item {key_item(var)}): its team's round reads Linear with it"
                           if any(a.startswith(f"{var}=") for a in prog) and "--env-from" in prog else
                           f"the plist gives the service no {var} (the keychain item {key_item(var)}): run `owlcode install`, "
                           "because a restart keeps the plist and the service then starts without that key")))
    return rows


HOME_FOLDERS = ("Library", ".Trash", "Desktop", "Documents", "Downloads", "Movies", "Music", "Pictures", "Public")
GITHUB_URL = re.compile(r"github\.com[:/]+([A-Za-z0-9-]+/[A-Za-z0-9._-]+?)(?:\.git)?/?$")


def clones_of(run: Run, user: str, home: str) -> tuple[list[tuple[str, str]], str]:
    """The clones in `home` up to three folders deep, as (folder, OWNER/NAME of its `origin`), and an error text ("" when
    the search ran). Each command runs as `user`, so root follows none of the user's links, and git runs nothing: the
    clone's `.git/config` is read as text. A worktree's `.git` is a file with no `origin`: its clone counts once. The
    search skips the folders that macOS makes in a home (HOME_FOLDERS): macOS's privacy control can refuse a read there."""
    skip = [arg for name in HOME_FOLDERS for arg in ("-o", "-path", f"{home}/{name}")][1:]
    found = run(["sudo", "-u", user, "-H", "/usr/bin/find", home, "-maxdepth", "3", "(", *skip, ")", "-prune", "-o",
                 "-type", "d", "-name", ".git", "-print", "-prune"], cwd=AGENT_CWD)
    if found.returncode != 0:
        return [], (found.stderr or found.stdout).strip()[:200] or f"find exited {found.returncode}"
    clones = []
    for git in found.stdout.split("\n"):
        if not git.strip():
            continue
        config = run(["sudo", "-u", user, "-H", "/bin/cat", "--", f"{git}/config"], cwd=AGENT_CWD)
        section = re.search(r'^\s*\[remote "origin"\]\s*$(.*?)(?=^\s*\[|\Z)', config.stdout, re.M | re.S)
        url = re.search(r"^\s*url\s*=\s*(\S+)", section.group(1), re.M) if section else None
        repo = GITHUB_URL.search(url.group(1)) if url else None
        if repo:
            clones.append((str(Path(git).parent), repo.group(1)))
    return clones, ""


def project_lines(svc: Service, run: Run) -> list[tuple[bool, str]]:
    """For each product (OWL-62, design b2): its agents' user exists, that user's home is mode 700, and the user holds no
    clone of another team's repository. A command that fails is a FAIL line with the reason."""
    rows: list[tuple[bool, str]] = []
    for project in svc.projects:
        team, user = project.team, project.user
        read = run(["dscl", ".", "-read", f"/Users/{user}", "NFSHomeDirectory"], cwd=AGENT_CWD)
        home = read.stdout.split(":", 1)[1].strip() if read.returncode == 0 and ":" in read.stdout else ""
        if not home:
            rows.append(_line(False, f"{team}: no user {user}: run `owlcode admin --project {team}`"))
            continue
        rows.append(_line(True, f"{team}: the user {user} exists"))
        mode = run(["stat", "-f", "%Lp", home], cwd=AGENT_CWD)
        found = mode.stdout.strip() if mode.returncode == 0 else ""
        rows.append(_line(found == "700", f"{team}: the home {home} of {user} is mode 700" if found == "700" else
                          f"{team}: the home {home} of {user} is mode {found or 'unknown'}, not 700: run `owlcode admin --project {team}`"))
        clones, error = clones_of(run, user, home)
        if error:
            rows.append(_line(False, f"{team}: cannot list the clones of {user} ({error})"))
            continue
        others = [(other, folder) for other in svc.projects if other.repo.lower() != project.repo.lower()
                  for folder, repo in clones if repo.lower() == other.repo.lower()]
        rows += [_line(False, f"{team}: the user {user} holds a clone of {other.team} ({folder})") for other, folder in others]
        if not others:
            rows.append(_line(True, f"{team}: the user {user} holds no other team's clone"))
    return rows


def profile_checks(svc: Service) -> list[tuple[str, str, str]]:
    """The (profile, key_check, credential) of the inference profile in force and of each other profile that its cells
    run on (`cell_profiles`), in that order: the credentials that a product's sessions use."""
    try:
        data = tomllib.loads(svc.config.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise Refused(f"cannot read {svc.config} ({e})") from None
    tables, name = data.get("profiles") or {}, data.get("inference_profile")
    if not isinstance(tables, dict) or not isinstance(name, str) or not isinstance(tables.get(name), dict):
        raise Refused(f"{svc.config}: inference_profile names no [profiles.NAME] table")
    out = []
    for profile in cell_profiles(name, tables[name], tables):
        table = tables[profile]
        out.append((profile, str(table.get("key_check") or ""), str(table.get("credential") or "keychain")))
    return out


USERS_HOME = re.compile(r"/Users/[^/]+(/.*)?")
ITEM_PREFIX = re.compile(r"[a-z][a-z0-9-]*-agent\.")  # agent_user.py SERVICE: the items of the user USER are `USER-agent.NAME`


def check_as(command: str, user: str, home: str) -> list[str]:
    """A profile's `key_check`, written for one user, as the command for `user`: the account after `-a` is `user`, an item
    after `-s` takes `user`'s prefix (`USER-agent.`), and a path in a home under /Users is the same path in `home`."""
    words = shlex.split(command)
    out = []
    for at, word in enumerate(words):
        before = words[at - 1] if at else ""
        if before == "-a":
            word = user
        elif before == "-s":
            word = ITEM_PREFIX.sub(f"{user}-agent.", word, count=1)
        elif found := USERS_HOME.fullmatch(word):
            word = home + (found.group(1) or "")
        out.append(word)
    return out


def key_lines(svc: Service, run: Run) -> list[tuple[bool, str]]:
    """For each product in a user of its own (not `[service] user`, whose keys the installed `--check` finds): the
    `key_check` of each profile that the cells use, run as that user with the paths of that user's home (OWL-71, design
    b2: the service's user cannot read that home, and its preflight runs no key check then). The exit code is the
    answer: no line prints what the command printed, so no value shows. A product whose user does not exist gets no line
    here: `project_lines` names it."""
    checks = profile_checks(svc)
    rows: list[tuple[bool, str]] = []
    for project in svc.projects:
        team, user = project.team, project.user
        if user == svc.user:
            continue
        read = run(["dscl", ".", "-read", f"/Users/{user}", "NFSHomeDirectory"], cwd=AGENT_CWD)
        home = read.stdout.split(":", 1)[1].strip() if read.returncode == 0 and ":" in read.stdout else ""
        if not home:
            continue
        for profile, command, credential in checks:
            found = bool(command.strip()) and run(["sudo", "-u", user, "-H", *check_as(command, user, home)], cwd=AGENT_CWD).returncode == 0
            if credential == "sign-in":
                rows.append(_line(found, f"{team}: {profile}'s sign-in is there for {user}" if found else
                                  f"{team}: {profile}'s sign-in is not there for {user}: run, as {user}, `pi`, then `/login`"))
            else:
                rows.append(_line(found, f"{team}: {profile}'s key is in {user}'s keychain" if found else
                                  f"{team}: {profile}'s key is not in {user}'s keychain: run, as {user}, "
                                  "`/usr/bin/python3 -I ~/agent_user.py user`"))
    return rows


def check(svc: Service, run: Run, out: Callable[[str], None]) -> int:
    """The installed copy's `orchestrator.py --check` as the agents' user, through the installed `agent_user.py run` (the
    keys of the service), then the install's own properties, each product's agents' user (`project_lines`) and the keys
    in that user's keychain (`key_lines`). The exit code is 1 when either part fails."""
    require_owner(svc, run)
    done = run(as_agent(svc, svc.install_dir, "--check"), cwd=AGENT_CWD)
    for text in (done.stdout, done.stderr):
        if text.strip():
            out(text.rstrip())
    rows = own_properties(svc, run) + project_lines(svc, run) + key_lines(svc, run)
    out("\n".join(line for _, line in rows))
    return 1 if done.returncode != 0 or not all(ok for ok, _ in rows) else 0


def restart(svc: Service, run: Run, out: Callable[[str], None]) -> int:
    """The service reads its config only at its start, so an edit of the installed config needs a restart. The local
    review's settings are written first, then one dry round of the installed copy on the config as it is: a config
    that it refuses never stops a running service, and the settings that round's copy of the service loaded stay as they
    were. Then `kickstart -k` in the agents' user domain, then `check`."""
    require_owner(svc, run)
    try:
        text = svc.config.read_text()
    except OSError as e:
        raise Refused(f"cannot read {svc.config} ({e.strerror})") from None
    written = {REVIEW_FILE: review_settings(text), PROFILE_FILE: profile_setting(text)}
    preflight = run(as_agent(svc, svc.install_dir, "--preflight"), cwd=AGENT_CWD)
    if preflight.returncode != 0:
        raise Refused(f"the installed copy's setup preflight failed on {svc.config}, so the service was not restarted and its local "
                      f"review's settings were not changed:\n{(preflight.stderr or preflight.stdout).strip()}")
    # The local review's settings follow an edit of the config, so a session that starts after this restart gets the
    # model that the config names. A first restart after a deleted file writes it too. The dry round's sessions read this
    # file, so it comes first; a round that refuses the config puts the file back, or the reviews that the running service's
    # sessions start would run on a model that the run line does not name (a finding of its local review).
    # The profile in force goes with them, so the Paseo daemon that the restarted service starts holds that profile.
    before = {}
    for name, content in written.items():
        target = svc.install_dir / name
        before[target] = run(["cat", str(target)])  # as install reads the installed COMMIT: root's file, the owner's command
        must(run, ["sudo", "tee", str(target)], stdin=content)
        must(run, ["sudo", "chown", "root:wheel", str(target)])
        must(run, ["sudo", "chmod", "644", str(target)])
    dry = run(as_agent(svc, svc.install_dir, "--once", "--dry-run"), cwd=AGENT_CWD)
    if dry.returncode != 0:
        for target, old in before.items():
            if old.returncode != 0:
                must(run, ["sudo", "rm", "-f", str(target)])
            else:
                must(run, ["sudo", "tee", str(target)], stdin=old.stdout)
        raise Refused(f"the installed copy's dry round failed on {svc.config}, so the service was not restarted and its local "
                      f"review's settings were not changed: {(dry.stderr or dry.stdout).strip()[:300]}")
    out(f"Wrote {svc.install_dir / REVIEW_FILE}: the local review's model and effort of each profile; and "
        f"{svc.install_dir / PROFILE_FILE}: the profile in force, for Paseo's daemon.")
    domain = f"user/{uid_of(svc, run)}"
    started = run(["sudo", "launchctl", "kickstart", "-k", f"{domain}/{svc.label}"])
    if started.returncode != 0:
        raise Refused(f"launchctl cannot restart {domain}/{svc.label} ({(started.stderr or started.stdout).strip()[:200]}). "
                      f"The job is not loaded: run `sudo launchctl bootstrap {domain} {svc.plist}`. The domain exists only "
                      f"after an SSH login of {svc.user} after a restart")
    out(f"Restarted {domain}/{svc.label}. A start that stops writes to {svc.crash_log}")
    return check(svc, run, out)


VALUE = r'"(?:[^"\\]|\\.)*"|[^\s#]+'  # a string or a bare word of a top-level line: the text that `set` replaces


def top_level(text: str) -> tuple[str, str]:
    """The config's text before its first table header, and the rest: the keys that `set` changes are in the first part."""
    match = re.search(r"(?m)^\[", text)
    at = match.start() if match else len(text)
    return text[:at], text[at:]


def parse_assignments(items: Sequence[str]) -> dict[str, str]:
    """`KEY=VALUE` words into a dict of the raw value texts. A value that is no TOML (`claude`) is a bare word: a string."""
    changes: dict[str, str] = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key.strip()) or not value.strip():
            raise Refused(f"'{item}' is not KEY=VALUE")
        changes[key.strip()] = value.strip()
    return changes


def set_keys(text: str, changes: dict[str, str]) -> str:
    """The config's text with each top-level key set to its new value, by a line edit: the comments and the order stay
    (`tomllib` cannot write). A key in a table, or one that the config lacks, is refused with the keys that it takes. A value
    keeps the type of the old one (a bool, a number or a string). The result is parsed again, and only the named keys differ."""
    head, rest = top_level(text)
    old = tomllib.loads(head)
    allowed = sorted(old)
    for key in changes:
        if key not in old:
            raise Refused(f"'{key}' is not a top-level key of {{config}}: only top-level keys can be set (a key in a table is edited "
                          f"by hand, then `owlcode restart`). Keys that take it: {', '.join(allowed)}")
    values: dict[str, object] = {}
    for key, raw in changes.items():
        try:
            value = tomllib.loads(f"v = {raw}")["v"]
        except tomllib.TOMLDecodeError:
            value = raw  # a bare word is a string
        if type(value) is not type(old[key]):
            raise Refused(f"{key} takes a {type(old[key]).__name__}, not '{raw}'")
        values[key] = value
    for key, value in values.items():
        literal = json.dumps(value) if isinstance(value, str) else str(value).lower() if isinstance(value, bool) else str(value)
        line = re.compile(rf"(?m)^({re.escape(key)}[ \t]*=[ \t]*)({VALUE})")
        head, count = line.subn(lambda m, lit=literal: str(m.group(1)) + str(lit), head, count=1)  # type: ignore[misc]
        if count != 1:
            raise Refused(f"cannot find the line of {key} in {{config}}")
    result = head + rest
    try:
        check = tomllib.loads(result)
    except tomllib.TOMLDecodeError as e:
        raise Refused(f"the edit makes {{config}} invalid TOML ({e}); nothing was written") from None
    if check != {**tomllib.loads(text), **values}:
        raise Refused("the edit changed more than the named keys; nothing was written")
    return result


def config_diff(old: str, new: str) -> list[str]:
    """The lines that changed, `-old` then `+new`, with no header and no context."""
    return [line for line in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0)
            if line[:1] in "+-" and not line.startswith(("+++", "---"))]


def set_config(svc: Service, items: Sequence[str], run: Run, out: Callable[[str], None]) -> int:
    """Change top-level keys of the installed config, then do what `restart` does, on the new text before it is
    written: print the diff, run the preflight and one dry round on a staged copy, and write the file and restart only when
    both pass. A failed check leaves the file and the service as they were."""
    require_owner(svc, run)
    if not items:
        raise Refused("name a key: owlcode set KEY=VALUE [KEY=VALUE ...]")
    try:
        old = svc.config.read_text()
    except OSError as e:
        raise Refused(f"cannot read {svc.config} ({e.strerror})") from None
    try:
        new = set_keys(old, parse_assignments(items))
    except Refused as e:
        raise Refused(str(e).replace("{config}", str(svc.config))) from None
    out(f"{svc.config}:")
    lines = config_diff(old, new)
    if not lines:
        out("no change: the config has these values already")
        return 0
    for line in lines:
        out(line)
    staged = svc.config.with_name(svc.config.name + ".new")
    must(run, ["sudo", "tee", str(staged)], stdin=new)
    try:
        must(run, ["sudo", "chmod", "644", str(staged)])
        preflight = run(as_agent(svc, svc.install_dir, "--preflight", config=staged), cwd=AGENT_CWD)
        if preflight.returncode != 0:
            raise Refused(f"the setup preflight failed on the new config, so nothing was written and the service was not restarted:\n"
                          f"{(preflight.stderr or preflight.stdout).strip()}")
        out("preflight: ok")
        dry = run(as_agent(svc, svc.install_dir, "--once", "--dry-run", config=staged), cwd=AGENT_CWD)
        if dry.returncode != 0:
            raise Refused(f"the dry round failed on the new config, so nothing was written and the service was not restarted: "
                          f"{(dry.stderr or dry.stdout).strip()[:300]}")
        text = dry.stdout
        starts = len(re.findall(r"\bwould start\b", text))
        wakes = len(re.findall(r"\bwould wake\b", text))
        out(f"dry round: ok ({starts} would start, {wakes} would wake)")
        must(run, ["sudo", "mv", str(staged), str(svc.config)])
        must(run, ["sudo", "chown", "root:wheel", str(svc.config)])
    finally:
        run(["sudo", "rm", "-f", str(staged)])
    out(f"written to {svc.config}")
    code = restart(svc, run, out)
    out("the service restarted" if code == 0 else "the service restarted, but its check failed: read the lines above")
    return code


def cell_parts(name: str, table: dict[str, Any], tables: dict[str, Any], cell: str) -> tuple[str, str, str]:
    """The model, the effort level ("" when the harness takes none) and the profile that runs one cell of `[profiles.NAME]`: a
    cell is [model, effort], [model, effort, PROFILE], [model, PROFILE] or [model] (`cell_profiles` reads the same forms)."""
    value = table.get(cell)
    if not isinstance(value, list) or not 1 <= len(value) <= 3 or not all(isinstance(v, str) for v in value):
        raise Refused(f"[profiles.{name}] {cell} is not a list of one to three words ([model, effort, PROFILE])")
    model, effort, other = value[0], "", name
    if len(value) == 3:
        effort, other = value[1], value[2]
    elif len(value) == 2 and value[1] in tables:
        other = value[1]
    elif len(value) == 2:
        effort = value[1]
    if other not in tables:
        raise Refused(f"[profiles.{name}] {cell} names {other!r}, which has no [profiles.{other}] table")
    return model, effort, other


def price_text(catalog: dict[str, Any], provider: object, model: str) -> str:
    """USD per million tokens, input/cached input/output, as `owlcode models` shows them; a model with no price in the
    catalog runs on a subscription."""
    found = ((catalog.get("providers") or {}).get(provider) or {}).get("models", {}).get(model)
    if found is None:
        return "not in the catalog"
    price = found.get("prices")
    return "/".join(f"${price[key]:g}" for key in ("input", "cached_input", "output")) if price else "subscription"


def profile_menu(config_text: str, catalog: dict[str, Any], credential: Callable[[str], bool]) -> tuple[list[str], list[str]]:
    """The profile names of the config, in its order, and the numbered list that the owner picks from: for each profile its
    harness, its provider, whether `credential(NAME)` passes, the model, effort and price of each of its four cells (and the
    profile that runs a cell, when it is another one), and its local review. The profile in force is marked."""
    try:
        data = tomllib.loads(config_text)
    except tomllib.TOMLDecodeError as e:
        raise Refused(f"the config does not parse ({e})") from None
    tables = data.get("profiles")
    if not isinstance(tables, dict) or not tables:
        raise Refused("the config names no [profiles.NAME] table: each inference profile needs one (orchestrator.example.toml)")
    names = list(tables)
    lines = ["USD per million tokens: input/cached input/output; \"subscription\" means no price per token.", ""]
    for number, name in enumerate(names, 1):
        table = tables[name]
        if not isinstance(table, dict):
            raise Refused(f"[profiles.{name}] is not a table")
        provider = table.get("provider")
        found = "ok" if credential(name) else "MISSING"
        mark = "  (in force)" if name == data.get("inference_profile") else ""
        lines.append(f"{number:>2}. {name}{mark}")
        lines.append(f"    harness {table.get('harness')}, provider {provider}, credential {found}")
        rows = []
        for cell in CELLS:
            model, effort, other = cell_parts(name, table, tables, cell)
            on = f" on {other}" if other != name else ""
            rows.append((cell, model, effort or "-", price_text(catalog, tables[other].get("provider"), model) + on))
        rows.append(("review", str(table.get("review_model")), str(table.get("review_effort")),
                     price_text(catalog, provider, str(table.get("review_model")))))
        widths = [max(len(row[i]) for row in rows) for i in range(3)]
        lines += [f"    {a:<{widths[0]}}  {b:<{widths[1]}}  {c:<{widths[2]}}  {d}" for a, b, c, d in rows]
        lines.append("")
    return names, lines


def switch_profile(svc: Service, items: Sequence[str], run: Run, ask: Callable[[str], str], out: Callable[[str], None]) -> int:
    """List the config's profiles and write the one that the owner picks as `inference_profile`, by `set_config`: the diff,
    the preflight and one dry round on a staged copy, the write and the restart only when both pass. A refused check leaves
    the old value in the file. With a NAME, no list and no question."""
    require_owner(svc, run)
    if len(items) > 1:
        raise Refused("name one profile: owlcode profile [NAME]")
    try:
        text = svc.config.read_text()
    except OSError as e:
        raise Refused(f"cannot read {svc.config} ({e.strerror})") from None
    catalog = run(["cat", str(svc.install_dir / "model-catalog.json")])
    if catalog.returncode != 0:
        raise Refused(f"cannot read the installed model catalog {svc.install_dir / 'model-catalog.json'}: run `owlcode install`")
    try:
        tables = tomllib.loads(text).get("profiles") or {}
    except tomllib.TOMLDecodeError as e:
        raise Refused(f"the config does not parse ({e})") from None
    if items and items[0] not in tables:
        raise Refused(f"'{items[0]}' is not a profile of {svc.config}. The profiles: {', '.join(tables) or 'none'}")

    def credential(name: str) -> bool:
        check = (tables.get(name) or {}).get("key_check")
        if not isinstance(check, str) or not check.strip():
            return False
        return run(["sudo", "-u", svc.user, "-H", *shlex.split(check)], cwd=AGENT_CWD).returncode == 0

    if items:
        name = items[0]
    else:
        names, lines = profile_menu(text, json.loads(catalog.stdout), credential)
        out(f"Inference profiles of {svc.config}:")
        for line in lines:
            out(line)
        try:
            answer = ask(f"Number of the profile to switch to (1-{len(names)}), or Enter to keep the one in force: ").strip()
        except EOFError:
            raise Refused("no answer: pass the profile's name (owlcode profile NAME) when no person types") from None
        if not answer:
            out("no change")
            return 0
        if not answer.isdigit() or not 1 <= int(answer) <= len(names):
            raise Refused(f"'{answer}' is not a number from 1 to {len(names)}; nothing was changed")
        name = names[int(answer) - 1]
    return set_config(svc, [f"inference_profile={json.dumps(name)}"], run, out)


def pause_file(svc: Service) -> Path:
    """The pause file that the service reads beside its config (orchestrator.py `pause_path`)."""
    return svc.config.with_name(svc.config.stem + ".paused")


def pause(svc: Service, run: Run, out: Callable[[str], None]) -> int:
    """A manual halt with the cause `paused by the owner`. The service finds the file in its next round: no new
    starts and no fix escalations, while wakes, closes and the queue go on. It lasts until `resume`."""
    require_owner(svc, run)
    target = pause_file(svc)
    must(run, ["sudo", "tee", str(target)], stdin="paused by the owner\n")
    must(run, ["sudo", "chown", "root:wheel", str(target)])
    must(run, ["sudo", "chmod", "644", str(target)])
    out("paused: no new starts until `owlcode resume`")
    return 0


def resume(svc: Service, run: Run, out: Callable[[str], None]) -> int:
    """Remove the pause file. The service's next round starts again."""
    require_owner(svc, run)
    must(run, ["sudo", "rm", "-f", str(pause_file(svc))])
    out("resumed: the service starts again in its next round")
    return 0


def keychain_of(svc: Service) -> str:
    """The keychain file that the service's check reads: the last word of `keychain_check` that names a keychain."""
    files = [w for w in shlex.split(svc.keychain_check) if w.endswith((".keychain-db", ".keychain"))]
    if not files:
        raise Refused(f"{svc.config}: [service] keychain_check names no keychain file (orchestrator.example.toml has the line)")
    return files[-1]


def run_tty(cmd: Sequence[str]) -> int:
    """A command on the terminal, not captured: `security unlock-keychain` asks for the password there."""
    return subprocess.run(list(cmd), check=False).returncode


def unlock(svc: Service, run: Run, tty: Callable[[Sequence[str]], int], out: Callable[[str], None]) -> int:
    """Unlock the agents' keychain in the audit session of their launchd domain, the session that the service runs
    in. `launchctl asuser UID` joins that session, and `sudo -u` keeps it. An unlock in a pane holds only in the pane's own
    session (measured 2026-11-19)."""
    require_owner(svc, run)
    uid = uid_of(svc, run)
    keychain = keychain_of(svc)
    out(f"Unlock the keychain of {svc.user} in user/{uid}: sudo asks for your password, then security for the keychain's.")
    code = tty(["sudo", "launchctl", "asuser", uid, "sudo", "-u", svc.user, "/usr/bin/security", "unlock-keychain", keychain])
    if code != 0:
        raise Refused(f"the unlock failed (exit {code}). The domain user/{uid} exists only after an SSH login of {svc.user} "
                      "after a restart: open a pane on that machine, then run this again")
    out(f"Unlocked. The service tries the keychain once a minute: run `owlcode check` after a minute; "
        f"its log: {svc.log_file}")
    return 0


def main(argv: Sequence[str] | None = None, run: Run = run_cmd, ask: Callable[[str], str] = input,
         out: Callable[[str], None] = print, tty: Callable[[Sequence[str]], int] = run_tty) -> int:
    parser = argparse.ArgumentParser(description="Install the orchestrator as a service: the commits, two dry rounds, a yes.")
    parser.add_argument("action", nargs="?", choices=("install", "check", "restart", "unlock", "set", "profile", "pause", "resume"),
                        default="install",
                        help="check: run the installed copy's health check as the agents' user, then check the install's own properties; "
                             "restart: a dry round on the installed config, a restart of the service, then check; "
                             "unlock: unlock the agents' keychain in the service's own session (after each restart of the Mac); "
                             "set KEY=VALUE ...: change top-level keys of the config, checked on a copy first, then a restart; "
                             "profile [NAME]: list the inference profiles and switch to the one you pick (or NAME), as set does; "
                             "pause / resume: hold the new starts until resume")
    parser.add_argument("items", nargs="*", help="KEY=VALUE words for `set`, the profile's NAME for `profile`")
    parser.add_argument("--config", type=Path, default=Path("/usr/local/etc/orchestrator.toml"))
    parser.add_argument("--checkout", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args(argv)
    try:
        svc = load_service(args.config)
        if args.action == "check":
            return check(svc, run, out)
        if args.items and args.action not in ("set", "profile"):
            raise Refused(f"{args.action} takes no arguments")
        if args.action == "set":
            return set_config(svc, args.items, run, out)
        if args.action == "profile":
            return switch_profile(svc, args.items, run, ask, out)
        if args.action == "pause":
            return pause(svc, run, out)
        if args.action == "resume":
            return resume(svc, run, out)
        if args.action == "restart":
            return restart(svc, run, out)
        if args.action == "unlock":
            return unlock(svc, run, tty, out)
        return install(svc, args.checkout, run, ask, out)
    except Refused as e:
        print(f"install.py: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
