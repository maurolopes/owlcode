#!/usr/bin/python3 -I
"""The macOS user where the coding agents run (the owner's calls of 2026-11-03).

The user NAME (USER below: the repository's name) is a Standard user (never an admin) whose primary group is `agents`, not `staff`. It holds the
project and scoped keys only: the implementer GitHub App's key (never the owner's login, which the ruleset `main` lets
bypass every rule), a Claude token, a Linear key and, when the owner stores one, the measuring OpenRouter key (it is
not production's, its own monthly cap; a session gets it as MEASURE_OPENROUTER_API_KEY, never as OPENROUTER_API_KEY, so a
model call needs the run's own step, as the product's rules for a measurement say), and when the owner stores one, the Ollama
Cloud key of the inference profiles `pi-ollama` (PROFILES, `session
--profile NAME`),
the LithosAI key of the profile `pi-lithosai`, and the ChatGPT sign-in that the profile `pi-chatgpt` runs on,
which pi makes itself with its own `/login`. It has no SSH key for the server and no Bitwarden session, so an
agent there cannot reach production (AGENTS.md "Autonomous runs"). The owner reads its work on GitHub, or with sudo, and
never runs its checkout (hooks, mise tasks, npm scripts) from the owner's account: that code would run as the owner.

Commands, in order:

  owlcode admin   (sudo AGENT_USER=NAME /usr/bin/python3 -I .workflow/approved_run.py .workflow/agent_user.py -- admin; NAME is [service] user) from the owner's checkout: the
                                                           group, the user, its home (700), a copy of this script there,
                                                           the owner's git identity
  sudo -iu NAME /usr/bin/python3 -I agent_user.py user     as the agent: mise, each harness's CLI (HARNESSES: Claude
                                                           Code, pi), the keys (typed at hidden prompts into the agent's
                                                           Keychain), the clone and its setup
  sudo /usr/bin/python3 -I .workflow/approved_run.py .workflow/agent_user.py -- check   one line per guard, `ok` or
                                                           `FAIL`; exit 1 on a FAIL
  sudo -iu NAME /usr/bin/python3 -I agent_user.py session [--profile NAME]   a session in the clone: the
                                                           profile's harness with its keys; a pane's `agent`
                                                           and `claude` are this command
  /usr/bin/python3 -I ~/agent_user.py run -- CMD [ARGS...]   as the agent: any command with the Linear key and the gh
                                                           shim first on PATH, in the folder it runs from; a
                                                           copy with a `bin/gh` beside it (the orchestrator's installed
                                                           copy, root's) puts that folder before ~/.local/bin
  /usr/bin/python3 -I ~/agent_user.py pi-signin --provider NAME   as the agent: whether pi is signed in to a provider that
                                                           signs itself in (`openai-codex` is ChatGPT Plus).
                                                           It tests that ~/.pi/agent/auth.json holds that provider's
                                                           entry, and never reads a token: the orchestrator's
                                                           `[profiles.NAME] key_check` of such a profile runs it
  /usr/bin/python3 -I ~/agent_user.py paseo-daemon      the orchestrator's service runs it: Paseo's daemon
                                                           with a session's keys on the inference profile in force
                                                           (`inference-profile.json`, which `install` and `restart`
                                                           write), and the keys of each other profile that its cells
                                                           run on (`cells` of `local-review.json`), so each agent it
                                                           starts has them; first the
                                                           service, loaded into the agent's domain when it is
                                                           installed and not loaded
  herdr machine add NAME --label NAME --remote-session default   once, from the owner's account: the agent
                                                           is a herdr machine, and `agent` or `claude` in its pane is a session

One user for each project (OWL-62, the owner's call of 2026-11-26, design b2): `owlcode admin --project TEAM` makes the
agents' user of one team, named by the `agent_user` of the team's table in the orchestrator's config (never `[service] user`),
and writes the team, its repository and the table's `paseo_port` into that user's home (PROJECT_FILE). That user's
`user` then clones that repository only, asks for that project's keys only (the implementer app installed on that
repository alone, a Linear key limited to that team, a Claude token of its own), and asks once for its Paseo daemon's own
password (Paseo keeps only its hash). Its git credential helper asks GitHub for tokens of that repository only. Its
`paseo-daemon` makes the daemon listen on this Mac's Tailscale address at that port. `admin` prints the config's
`paseo_host` and the step that stores the same password in the orchestrator's keychain at a hidden prompt, never the
password. The user is its own herdr machine, with its own key and sshd file, as every agents' user is.

The owner runs `admin`, `check` and `models` through the launcher `.workflow/approved_run.py`: this script
merges on the reviewer's verdict, and the launcher shows what changed in it since the owner's last yes and asks one yes
before it runs this script with the owner's rights.

herdr detects an agent by its pane's foreground process, which is `sudo` behind `sudo -iu`, so the owner's herdr reaches
the agent over SSH on 127.0.0.1: sshd lets only this user in, from this Mac, with the owner's key for it, and
closes every forwarding, so the owner's ssh-agent (it holds the server's key) never reaches the agent.

herdr in the agent's account is herdr's own install, `~/.local/bin/herdr` (the owner's call of 2026-11-04):
`herdr machine add` puts it there, and herdr's update writes over the file it runs from. A copy in mise went out of step
with mise's own record (a "0.9.3" folder held 0.9.5), and a pin went stale at each release. Client and server versions
need not match: herdr checks what each side supports. The first `user` that installs herdr this way deletes mise's herdr, the file the
agent's server started from: restart that server first, when no agent works (the owner's call of 2026-11-04).

Paseo in the agent's account is the owner's Paseo app (the owner's call of 2026-11-17): `user` links
~/.local/bin/paseo to the CLI inside /Applications/Paseo.app, a shell script that runs on the app's own runtime. Every
user can run it and only the owner can write it, so the agents run code that they cannot change, and the app's update
updates both accounts. An npm copy went out of step unseen (0.15.4 beside the app's 0.16.1) and lived in one Node
version of mise, so a change of NODE_VERSION would take it away: `user` removes it. After an update of the app the agent's
daemon runs the old code until it starts again, and a restart kills the agents that run: `check` shows the three
versions and names the restart, and never restarts the daemon itself.

The owner's phone reaches the agent's own herdr with Heeler (the owner's calls of 2026-11-04): the agent's
account only, never the owner's, and over Tailscale only. sshd also lets the agent in from the tailnet's addresses, and
allows it local forwarding (Heeler reads herdr.sock through a stream; OpenSSH lets a stream through only while
AllowTcpForwarding allows local forwarding too, measured 2026-11-04). Only a key with `port-forwarding`, a phone key the
owner approved, gets it, and from a shell it gives nothing more; listening, agent forwarding and tunnels stay closed. The owner adds
the phone's key to the agent's authorized_keys; `admin` asks the owner for each key it has not seen, keeps the approved
ones in /etc/ssh/NAME-phones (root's: the agent owns its authorized_keys and could add a key of its own), gives them
the tailnet's `from=`, and drops every other key, a pairing's one-time key included.

`admin` and `check` never change the owner's home folder: `check` fails while it is not 700 (`chmod 700 ~`). Every command
can run again: a step that is done is skipped. The keys stay in the agent's own keychain (agent.keychain-db); none is written to a file,
passed on a command line or printed. The GitHub token is the app's installation token (one hour), made when `gh` or git
needs one: `openssl` signs the app's JWT (the standard library cannot sign RS256), and the token waits in the Keychain
until five minutes before it ends.
The orchestrator's installed copy may use the orchestrator's own GitHub App instead (OWL-70, `service-app.json` beside
it): its `gh` then mints a token of that app for the repository of each call.

Standard library only, and Python 3.9 (the system's /usr/bin/python3, the only Python the agent has before mise).
"""

from __future__ import annotations

import argparse
import base64
import calendar
import fcntl
import fnmatch
import getpass
import hashlib
import ipaddress
import json
import os
import plistlib
import pwd
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import termios
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import NamedTuple

# The one name (the owner's call of 2026-11-03): the user, its clone and its Keychain service take the repository's
# name as it is. A name that a macOS short name, a GitHub repository or a Linux user (a VPS later) cannot take is refused,
# never edited. The file holds no name of its own project (the owner's call of 2026-11-24): `agent_name` finds it
# where the script runs.
GITHUB_OWNER = "maurolopes"  # the GitHub account of the loop's repositories
NAME_RULE = re.compile(r"[a-z]([a-z0-9-]{0,30}[a-z0-9])?")
NAME_RULE_TEXT = "lowercase letters and digits, dashes between them, a letter first, 32 characters at most"
GROUP = "agents"  # every project's agent user is in it
GID = 600
CHECKOUT = Path(__file__).resolve().parent.parent  # the owner's checkout, when this is its .workflow/agent_user.py


def project_name(repo: str) -> str:
    name = repo.rsplit("/", 1)[-1]
    if not NAME_RULE.fullmatch(name):
        raise ValueError(f"the repository's name {name!r} cannot name a user: {NAME_RULE_TEXT}")
    return name


def origin_name(checkout: Path) -> str:
    """The name of the repository that the `origin` URL of `checkout` names, or "" when there is none. It reads git's
    files and runs no git: as root, this script runs nothing of the owner's checkout (its config can name a program)."""
    git = checkout / ".git"
    try:
        if git.is_file():  # a worktree: `.git` names its own folder, and that folder's `commondir` the shared one
            folder = Path(git.read_text().split("gitdir:", 1)[1].strip())
            folder = folder if folder.is_absolute() else checkout / folder
            common = folder / "commondir"
            git = folder / common.read_text().strip() if common.is_file() else folder
        config = (git / "config").read_text()
    except (OSError, IndexError):
        return ""
    section = re.search(r'^\s*\[remote "origin"\]\s*$(.*?)(?=^\s*\[|\Z)', config, re.M | re.S)
    url = re.search(r"^\s*url\s*=\s*(\S+)", section.group(1), re.M) if section else None
    if not url:
        return ""
    path = url.group(1).rstrip("/")
    path = path[:-len(".git")] if path.endswith(".git") else path
    return re.split(r"[/:]", path)[-1]


def agent_name(environ: Mapping[str, str], uid: int, checkout: Path) -> str:
    """The agents' user name (the owner's call of 2026-11-24). AGENT_USER in the environment comes first (the tests, a
    second agents' user). In an account whose primary group is GROUP, it is that account's login name, read from the uid:
    never $USER, which keeps the caller's name after `su` from the owner's account. Else (root through sudo, the owner) it
    is the name of the repository that the checkout's `origin` names. So the guards that refuse another account compare
    the account with this name: in effect they check the primary group."""
    named = environ.get("AGENT_USER", "")
    if not named:
        try:
            account = pwd.getpwuid(uid)
        except KeyError:
            account = None
        named = account.pw_name if account and account.pw_gid == GID else origin_name(checkout)
    if not named:
        raise ValueError(f"no agents' user name: {checkout} has no `origin` remote, and this account is not in the group "
                         f"{GROUP}; set AGENT_USER (with sudo: sudo AGENT_USER=NAME ...)")
    return project_name(named)


try:
    USER = agent_name(os.environ, os.getuid(), CHECKOUT)
except ValueError as e:
    sys.exit(f"agent_user.py: {e}")


# A project's own agents' user (OWL-62, the owner's call of 2026-11-26, design b2): `owlcode admin --project TEAM` names
# the team, its repository and its Paseo daemon's port, and `admin` writes them as this file in the user's home. `user`
# clones that repository only, the git credential helper asks GitHub for tokens of that repository only, and
# `paseo-daemon` makes the daemon listen on that port. An agents' user with no such file serves every row of "The teams"
# (`loop_repos`), as before.
PROJECT_FILE = "agent-project.json"
TEAM_RULE = re.compile(r"[A-Z][A-Z0-9]{0,15}")  # a Linear team's key
OWNER_RULE = re.compile(r"[A-Za-z0-9]([A-Za-z0-9-]{0,37}[A-Za-z0-9])?")  # a GitHub account's name


class Project(NamedTuple):
    team: str  # the Linear team's key
    repo: str  # OWNER/NAME: the one repository of the user's clone and its tokens
    paseo_port: int = 0  # the port of the user's Paseo daemon; 0: the daemon keeps the address it has


def project_from(found: object, where: str = PROJECT_FILE) -> Project:
    """The project of the parsed file (or of `admin`'s arguments); ValueError names what is wrong."""
    if not isinstance(found, dict):
        raise ValueError(f"{where} is not a JSON object")
    team, repo, port = found.get("team"), found.get("repo"), found.get("paseo_port", 0)
    if not isinstance(team, str) or not TEAM_RULE.fullmatch(team):
        raise ValueError(f"{where}: team {team!r} is not a team's key (capital letters and digits, a letter first)")
    owner, _, name = repo.partition("/") if isinstance(repo, str) else ("", "", "")
    if not OWNER_RULE.fullmatch(owner) or not NAME_RULE.fullmatch(name):
        raise ValueError(f"{where}: repo {repo!r} is not OWNER/NAME with a NAME of {NAME_RULE_TEXT}")
    if type(port) is not int or not (port == 0 or 1024 <= port <= 65535):
        raise ValueError(f"{where}: paseo_port {port!r} is not a port from 1024 to 65535")
    return Project(team, f"{owner}/{name}", port)


def own_project(uid: int, home: Path) -> Project | None:
    """The project of the agents' user that runs this, from its home's PROJECT_FILE; None for another account (root through
    sudo, the owner: they never read a file of the agent's home) and for a user with no such file."""
    try:
        if pwd.getpwuid(uid).pw_name != USER:
            return None
    except KeyError:
        return None
    path = home / PROJECT_FILE
    if not path.is_file():
        return None
    try:
        return project_from(json.loads(path.read_text()))
    except (OSError, ValueError) as e:
        raise ValueError(f"{path}: {e}; run `owlcode admin --project TEAM` again") from None


try:
    PROJECT = own_project(os.getuid(), Path.home())
except ValueError as e:
    sys.exit(f"agent_user.py: {e}")
REPO = PROJECT.repo if PROJECT else f"{GITHUB_OWNER}/{USER}"
# A row of the table "The teams" in an AGENTS.md: the team's key, then its repository (OWNER/NAME)
TEAM_ROW = re.compile(r"^\|\s*[A-Z][A-Z0-9]*\s*\|\s*([A-Za-z0-9-]+/[A-Za-z0-9._-]+)\s*\|", re.M)


def team_repos(agents_md: str) -> list[str]:
    """The repositories of the table "The teams" in the text of an AGENTS.md, in the order of its rows."""
    section = agents_md.split("\n## The teams\n", 1)[1:]
    return TEAM_ROW.findall(section[0].split("\n## ", 1)[0]) if section else []


def loop_repos(agents_md: str, project: Project | None = None) -> tuple[str, ...]:
    """The repositories of the loop: the agents' repository (REPO) and the rows of "The teams" in the product's
    AGENTS.md (`agents_md`, the text of the agents' clone), which name the loop's repository too. This file names no
    repository: the product's table holds them. The app's installation must list exactly these. A project's own user
    (`project`, PROJECT by default) has its project's repository only, whatever the table says."""
    project = project or PROJECT
    if project:
        return (project.repo,)
    return tuple(dict.fromkeys((REPO, *team_repos(agents_md))))


def clone_agents_md(home: Path) -> str:
    """The AGENTS.md of the agents' clone (`home`/CLONE), read by the account that runs this; "" when it cannot be read."""
    try:
        return (home / CLONE / "AGENTS.md").read_text()
    except (OSError, UnicodeDecodeError):
        return ""

FULL_NAME = f"{USER} agent"
CLONE = Path(REPO.rsplit("/", 1)[-1])  # under the agent's home: ~/NAME (the repository's name), never the owner's own checkout
SERVICE = f"{USER}-agent"  # the Keychain items' service prefix
MISE_VERSION = "v2026.10.10"
GH_VERSION = "2.96.0"  # the agent's own gh, in its global mise config: the project's mise.toml has none
SSHD_DIR = Path("/etc/ssh/sshd_config.d")  # macOS's sshd_config includes it first: sshd keeps the first value it reads
SSH_KEY = f"{USER}-agent"  # the owner's key for the agent, in the owner's ~/.ssh
KEY_OPTIONS = 'from="127.0.0.1,::1",restrict,pty'  # restrict: no forwarding of any kind; pty: herdr's shells
TAILNET = ("100.64.0.0/10", "fd7a:115c:a1e0::/48")  # Tailscale's addresses: the owner's phone, never the home LAN
# a phone's key (Heeler): from the tailnet only; port-forwarding gives back the stream to herdr.sock, which `restrict` takes
# away; sshd keeps listening, agent forwarding and tunnels closed for the agent whatever a key says
PHONE_OPTIONS = f'from="{",".join(TAILNET)}",restrict,port-forwarding,pty'
PHONES = Path("/etc/ssh") / f"{USER}-phones"  # the keys the owner approved, root's; never in sshd_config.d: sshd reads all of it
ALLOWED = (f"{USER}@127.0.0.1", f"{USER}@::1", *(f"{USER}@{net}" for net in TAILNET))
PASEO_APP = Path("/Applications/Paseo.app/Contents/Resources/bin/paseo")  # the owner's app's CLI
PASEO_NPM = "@getpaseo/cli"  # the npm copy that `user` removes
VERSION = re.compile(r"[0-9]+(\.[0-9]+){1,3}([-+][0-9A-Za-z.-]+)?")
NODE_VERSION = "24.16.0"  # Heeler's herdr plugin runs on Node 20 or newer; the project's mise.toml pin (a test keeps them equal)
# Every line of the agent's .zshenv: an SSH command reads no other file, and a $PATH here took the owner's PATH
ZSHENV = 'export PATH="$HOME/.local/bin:$HOME/.local/share/mise/shims:/usr/bin:/bin:/usr/sbin:/sbin"\n'
SCRIPT = "agent_user.py"  # the copy in the agent's home
CLAUDE_ALIAS = f'alias claude="/usr/bin/python3 -I $HOME/{SCRIPT} session"'  # a herdr pane's `claude` gets the keys
# The neutral name of a session: `agent --profile NAME` starts the profile's harness. `claude` stays, and with no
# --profile both start DEFAULT_PROFILE's harness, Claude Code.
AGENT_ALIAS = f'alias agent="/usr/bin/python3 -I $HOME/{SCRIPT} session"'
# herdr's `agent start --kind pi` types `pi ARGS` into the pane (measured 2026-11-23), so the pane's `pi` is a command
# of this script too. It takes a pi profile only: a `pi` typed with no profile must not start Claude Code.
PI_ALIAS = f'alias pi="/usr/bin/python3 -I $HOME/{SCRIPT} pi"'
OK, FAIL = "ok", "FAIL"
API = "https://api.github.com"

# The keys the agent's Keychain holds: name, prompt, whether the prompt hides the input, the shape a typed value must have.
KEYS = (
    ("app-client-id", "The implementer app's client ID (in your account: gh variable get IMPLEMENTER_APP_CLIENT_ID): ",
     False, r"Iv[0-9A-Za-z.]{8,40}"),
    ("app-key", "The implementer app's private key: in your account `pbcopy < KEY.pem`, then paste it here (nothing shows; it ends by itself at the END line): ",
     True, r"[A-Za-z0-9+/=]{400,8000}"),
    ("claude-token", "The token that `claude setup-token` printed: ", True, r"sk-ant-[A-Za-z0-9_-]{20,400}"),
    ("linear-key", "The Linear API key (Linear: Settings, Security & access, Personal API keys): ", True,
     r"lin_api_[A-Za-z0-9]{20,100}"),
    # any value, no shape check: the owner's own key's format is not measured (the owner's answer, 2026-11-22)
    ("lithosai-key", "The LithosAI API key of the agents, never a product key (console.lithosai.cloud: API Keys; Enter skips it): ",
     True, r".+"),
    ("measure-key", "The measuring OpenRouter key, never the production key (OpenRouter: Settings, API Keys; Enter skips it): ",
     True, r"sk-or-[A-Za-z0-9-]{20,200}"),
    ("ollama-key", "The Ollama Cloud API key of the agents, never a product key (ollama.com: Settings, Keys; Enter skips it): ",
     True, r"[A-Za-z0-9._-]{20,200}"),
)
# A key that `user` may leave out: an empty answer stores nothing, and a session then has no such variable
OPTIONAL_KEYS = ("measure-key", "ollama-key", "lithosai-key")
# What every session gets for each key, as an environment variable: the Keychain item, the variable. The model's key is
# the inference profile's (PROFILES). The Linear key is LINEAR_API_KEY: it reaches every team of this workspace.
SESSION_KEYS = (("linear-key", "LINEAR_API_KEY"),)
SESSION_OPTIONAL_KEYS = (("measure-key", "MEASURE_OPENROUTER_API_KEY"),)


class Harness(NamedTuple):
    """A harness (the project "Runs on any harness"): the CLI that `user` installs and `session` starts, the API
    styles it talks, the variable that carries a profile's base URL, the Keychain items that its own CLI makes, and its own
    settings that `user` writes."""

    binary: str  # the CLI in ~/.local/bin: `session` starts it, and `user` installs it when it is not there
    version: str  # the version that `user` installs (a CLI that updates itself moves on from there)
    install: str  # the shell command that installs the CLI, with `{version}`
    apis: tuple[str, ...]  # the API styles it talks ("anthropic", "openai"): a profile's `api` is one of them
    base_url: str  # the variable that carries a profile's base URL to it
    provider_variables: tuple[str, ...]  # every variable it reads for a key, a credential, a provider or its address: a
    # caller's value of any of them never reaches a run (CALLER_KEYS)
    key_setup: tuple[tuple[str, tuple[str, ...], str], ...]  # an item of KEYS that the CLI makes: the item, the CLI's
    # arguments that print the value, and the line that `user` prints first
    settings_file: str  # its settings file under the home folder, "" when `user` writes none
    settings: tuple[tuple[str, object], ...]  # the keys that `user` sets there, with their values
    trust_file: str = ""  # a JSON file under the home folder where `user` marks each clone of loop_repos() trusted, so a
    # session without a person loads the project's own files (pi: `.agents/skills`, `.pi/settings.json`); "" for none
    provider_file: str = ""  # the JSON file under the home folder where `user` writes each profile of the harness as a
    # provider (PROVIDER_ENTRIES: pi's models.json), since the harness reads a provider's address
    # and models from that file, not from a variable
    updates_itself: bool = False  # the CLI updates itself (Claude Code): `user` installs it only when it is missing; another
    # CLI stays at this entry's version, and `user` installs it again when the binary reports another one


# The harnesses. Claude Code and pi ship. OpenCode was dropped (DECISIONS.md "OpenCode was dropped").
# Claude Code's entry holds what `user` did before: claude.ai's installer into ~/.local/bin (`bash -s VERSION`
# installs that version; the CLI then updates itself), `claude setup-token` for the subscription's token, and the one
# setting that passes its one-time question about the bypass mode (measured 2026-11-20, Claude Code 2.1.328: its default
# answer is "No, exit", so a herdr pane on a profile with `bypassPermissions` waits there for ever; the mode
# itself stays the orchestrator's choice for each profile).
# Claude Code's key, credential, address, header and provider-switch variables: from code.claude.com/docs/en/env-vars, read
# 2026-11-21. A caller's Bedrock switch and token, or an Authorization header, would send a profile's
# calls with the caller's credential.
CLAUDE_CODE_PROVIDER_VARIABLES = (
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_OAUTH_REFRESH_TOKEN",
    "ANTHROPIC_CUSTOM_HEADERS", "ANTHROPIC_PROFILE", "ANTHROPIC_ORGANIZATION_ID", "ANTHROPIC_FEDERATION_RULE_ID",
    "ANTHROPIC_WORKSPACE_ID", "CLAUDE_CODE_CLIENT_CERT", "CLAUDE_CODE_CLIENT_KEY", "CLAUDE_CODE_CLIENT_KEY_PASSPHRASE",
    "ANTHROPIC_BASE_URL", "ANTHROPIC_AWS_BASE_URL", "ANTHROPIC_BEDROCK_BASE_URL", "ANTHROPIC_BEDROCK_MANTLE_BASE_URL",
    "ANTHROPIC_FOUNDRY_BASE_URL", "ANTHROPIC_GOOGLE_CLOUD_BASE_URL", "ANTHROPIC_VERTEX_BASE_URL",
    "ANTHROPIC_AWS_API_KEY", "ANTHROPIC_FOUNDRY_API_KEY", "ANTHROPIC_FOUNDRY_AUTH_TOKEN", "AWS_BEARER_TOKEN_BEDROCK",
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_USE_MANTLE",
    "CLAUDE_CODE_USE_ANTHROPIC_AWS", "CLAUDE_CODE_USE_ANTHROPIC_GOOGLE_CLOUD", "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
)
# pi's key, credential, address and provider variables: from `pi --help` and its docs (providers.md,
# environment-variables.md), read 2026-11-21 at 1.5.2. PI_CODING_AGENT_DIR names the folder whose models.json gives the
# providers, so a caller's value would send a profile's calls elsewhere.
PI_PACKAGE = "@earendil-works/pi-coding-agent"
# pi's own sign-ins: its `/login` writes each provider's tokens here, mode 600, and it refreshes them itself
# under a lock on the file. Nothing in this script prints, copies or returns one.
PI_AUTH_FILE = ".pi/agent/auth.json"
PI_PROVIDER_VARIABLES = (
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_OAUTH_TOKEN", "ANTHROPIC_FEDERATION_RULE_ID",
    "ANTHROPIC_IDENTITY_TOKEN_FILE", "ANTHROPIC_ORGANIZATION_ID", "ANTHROPIC_SERVICE_ACCOUNT_ID", "ANTHROPIC_WORKSPACE_ID",
    "ANT_LING_API_KEY", "OPENAI_API_KEY", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_BASE_URL", "AZURE_OPENAI_RESOURCE_NAME",
    "AZURE_OPENAI_API_VERSION", "AZURE_OPENAI_DEPLOYMENT_NAME_MAP", "DEEPSEEK_API_KEY", "NVIDIA_API_KEY", "GEMINI_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS", "GROQ_API_KEY", "CEREBRAS_API_KEY", "XAI_API_KEY", "FIREWORKS_API_KEY",
    "TOGETHER_API_KEY", "BASETEN_API_KEY", "OPENROUTER_API_KEY", "AI_GATEWAY_API_KEY", "ZAI_API_KEY",
    "ZAI_CODING_CN_API_KEY", "MISTRAL_API_KEY", "MINIMAX_API_KEY", "MINIMAX_CN_API_KEY", "MOONSHOT_API_KEY",
    "OPENCODE_API_KEY", "KIMI_API_KEY", "META_API_KEY", "CLOUDFLARE_API_KEY", "CLOUDFLARE_ACCOUNT_ID",
    "CLOUDFLARE_GATEWAY_ID", "QWEN_TOKEN_PLAN_API_KEY", "QWEN_TOKEN_PLAN_CN_API_KEY", "XIAOMI_API_KEY",
    "XIAOMI_TOKEN_PLAN_CN_API_KEY", "XIAOMI_TOKEN_PLAN_AMS_API_KEY", "XIAOMI_TOKEN_PLAN_SGP_API_KEY", "COPILOT_GITHUB_TOKEN",
    "HF_TOKEN", "RADIUS_API_KEY", "TYPESAFE_API_KEY", "AWS_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN", "AWS_BEARER_TOKEN_BEDROCK", "AWS_REGION", "AWS_WEB_IDENTITY_TOKEN_FILE", "PI_CODING_AGENT_DIR",
)
# The remains of an OpenCode that an earlier `user` installed: `user` removes them.
OPENCODE_PACKAGE = "opencode-ai"
OPENCODE_CONFIG_DIR = ".config/opencode"
HARNESSES = {
    "claude-code": Harness(
        "claude", "2.1.328", "curl -fsSL https://claude.ai/install.sh | bash -s {version}", ("anthropic",),
        "ANTHROPIC_BASE_URL", CLAUDE_CODE_PROVIDER_VARIABLES,
        (("claude-token", ("setup-token",),
          "Claude: sign in on the page that opens; the command prints a token. Copy it for the next prompt."),),
        ".claude/settings.json", (("skipDangerousModePermissionPrompt", True),), updates_itself=True),
    # pi: npm's package, installed under ~/.local (the CLI at ~/.local/bin/pi), outside mise's Node, so a new
    # NODE_VERSION keeps it. A copy in mise's Node (an `npm install -g` by hand) goes first: an interactive shell puts mise's
    # Node first on PATH, and Paseo starts the `pi` that PATH finds. pi has no permission modes and no Keychain step. Its
    # provider is a profile's entry in ~/.pi/agent/models.json (`pi_provider`); its key comes from the profile's variable.
    # It loads a project's own files (`.agents/skills`, `.pi/`, where the guards' adapter goes) only in a project
    # that it trusts, and a run cannot answer its trust question, so `user` trusts the agents' clones (~/.pi/agent/trust.json).
    "pi": Harness(
        "pi", "1.5.2",
        f'"$HOME/.local/bin/mise" exec node@{NODE_VERSION} -- sh -c \'npm uninstall -g {PI_PACKAGE} && '
        f'npm install -g --prefix "$HOME/.local" {PI_PACKAGE}@{{version}}\'', ("openai", "anthropic"), "",
        PI_PROVIDER_VARIABLES, (), "", (), ".pi/agent/trust.json", ".pi/agent/models.json"),
}


class Model(NamedTuple):
    """A model ID and its settings in the format of its harness's provider file (pi's models.json)."""

    id: str
    settings: tuple[tuple[str, object], ...] = ()


class Profile(NamedTuple):
    """An inference profile: the harness that runs the session, and the provider in that harness's
    terms: its API style, its base URL, its key and the variable that carries the key, and its other variables. A profile
    may run on a provider that the harness itself knows and that signs itself in (`provider`): then it has no key,
    no variable and no entry in the harness's provider file."""

    harness: str  # the harness that `session` starts (HARNESSES)
    api: str  # the provider's API style, one of the harness's `apis`
    base_url: str  # the provider's address, "" for the harness's own default
    key: str  # the Keychain item of its key
    variable: str  # the variable that carries that key to the harness
    env: tuple[tuple[str, str], ...]  # the provider's other variables in the harness's terms, fixed values (no key)
    budget: str  # the local review's --max-budget-usd: it counts Anthropic's prices, so "" on another provider
    review_cli: str  # the local review's command, in the harness's print mode: `{model}`, `{effort}` and
    # `{budget}` (the budget's flag, or nothing) are filled in. It reads the prompt on stdin, has read-only tools, and prints
    # a reply that holds the JSON; the script finds it in any printed text. Another harness's profile names its own.
    models: tuple[Model, ...] = ()  # the catalog's models, for a harness that needs a provider file
    provider: str = ""  # the provider's own name in the harness, for one that the harness knows already and that signs
    # itself in (pi's `openai-codex`: the ChatGPT subscription). The harness's catalog holds that provider's
    # address and models, an entry of `provider_file` under that name would replace them, and the CLI keeps the sign-in
    # itself: so the profile writes no entry, has no key and no variable, and `models` stays empty


# The local review's command in Claude Code's print mode: the schema flag makes it print the object under `structured_output`.
CLAUDE_REVIEW_CLI = ('claude -p --model {model} --effort {effort} --tools "Read,Grep,Glob" --strict-mcp-config '
                     '--setting-sources user --no-session-persistence --output-format json --json-schema "$LOCAL_REVIEW_SCHEMA"{budget}')
# The inference profiles: the orchestrator's `inference_profile` names one and gives it to the session as
# `session --profile NAME` (its `{profile}` place); `paseo-daemon` gives it to Paseo's agents. A `claude` or
# an `agent` typed by hand gets DEFAULT_PROFILE. Here and not in the orchestrator's config: the review's command goes into
# every environment that `keys_env` builds, and Python 3.9 reads no TOML. The orchestrator's [profiles.NAME] tables hold
# each profile's run cells and its review's model and effort level (a test keeps the names equal). Each profile names its
# harness, provider and models: a run is not tied to Claude Code.
# The local review's command in pi's print mode: the prompt on stdin (measured), only the read-only tools,
# none of the files that need trust (`--no-approve`: no setting, MCP server, extension, skill, prompt or system prompt of
# .pi/, and no .agents/skills; the context files, AGENTS.md and CLAUDE.md, still load: pi's docs, security.md), no MCP
# server, no saved session. pi has no schema flag: the prompt gives the schema, and the script finds the JSON in the printed text.
# The model is `PROVIDER/MODEL`, the provider being the profile's name in pi's models.json (`pi_provider`).
PI_REVIEW_CLI = ("pi -p --no-session --no-approve --no-extensions --no-mcp --tools read,grep,find,ls --model {provider}/{model} "
                 "--thinking {effort}{budget}")
METRICS_EXTENSION = "agent-metrics.ts"
METRICS_SOURCE = Path(__file__).resolve().parent.parent / ".agents" / "metrics" / "pi.ts"
CATALOG_FILE = "model-catalog.json"
CATALOG_PATH = Path(__file__).resolve().with_name(CATALOG_FILE)
if CATALOG_PATH.parent.name == ".workflow":
    CATALOG_PATH = CATALOG_PATH.parent.parent / "orchestrator" / CATALOG_FILE
CATALOG = json.loads(CATALOG_PATH.read_text())


def catalog_profile(harness: str, provider_id: str) -> Profile:
    provider = CATALOG["providers"][provider_id]
    connection = provider["connections"][harness]
    sign_in = connection.get("sign_in", "")
    models = []
    env: tuple[tuple[str, str], ...] = ()
    if harness == "pi":
        env = (("PI_OFFLINE", "1"), ("PI_TELEMETRY", "0"))
        if not sign_in:  # Keep pi's built-in OAuth models intact.
            for model_id, model in provider["models"].items():
                group = model["efforts"].get(harness)
                if not group:
                    continue
                settings = {key: model[field] for key, field in (("contextWindow", "context_window"), ("maxTokens", "max_output"))
                            if model[field] is not None}
                settings["thinkingLevelMap"] = CATALOG["efforts"][group]
                if group in CATALOG["pi_compat"]:
                    settings["compat"] = CATALOG["pi_compat"][group]
                models.append(Model(model_id, tuple(settings.items())))
    review_cli = PI_REVIEW_CLI if harness == "pi" else CLAUDE_REVIEW_CLI
    return Profile("pi" if harness == "pi" else "claude-code", connection["api"],
                   "" if connection.get("use_default_url") else connection["base_url"],
                   provider["keychain"] or "", connection["key_variable"] or "", env,
                   "4" if provider_id == "anthropic" else "", review_cli, tuple(models), sign_in)


PROFILES = {name: catalog_profile(route["harness"], route["provider"]) for name, route in CATALOG["profiles"].items()}
DEFAULT_PROFILE = "claude"
# The variables of an OpenAI-compatible provider: pi reads some of them,
# so they leave every caller's environment, whatever the harness's entry lists.
OPENAI_VARIABLES = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_API_BASE", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID")
# Every key or provider variable of the caller's environment goes before the Keychain's are set: a production model key
# never reaches a run, and a caller's provider variables never send a profile's calls elsewhere. It holds each harness's
# provider variables and base-URL variable, and each profile's key and provider variables, whatever the harness.
CALLER_KEYS = tuple(dict.fromkeys((
    "ANTHROPIC_API_KEY", "GH_TOKEN", "GITHUB_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "LINEAR_API_KEY", "OPENROUTER_API_KEY",
    "MEASURE_OPENROUTER_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OLLAMA_API_KEY", "LITHOSAI_API_KEY", *OPENAI_VARIABLES,
    *(var for h in HARNESSES.values() for var in h.provider_variables),
    *sorted({h.base_url for h in HARNESSES.values() if h.base_url}),
    *sorted(({p.variable for p in PROFILES.values()} | {var for p in PROFILES.values() for var, _ in p.env}) - {""}))))


def provider_env(profile: Profile) -> tuple[tuple[str, str], ...]:
    """The profile's provider variables in its harness's terms, without the key: the base URL (when the profile names one
    and its harness reads it from a variable; pi reads it from its models.json, `pi_provider`), then its other variables."""
    variable = HARNESSES[profile.harness].base_url
    base = ((variable, profile.base_url),) if profile.base_url and variable else ()  # pi: its provider file
    return (*base, *profile.env)


PI_APIS = {"openai": "openai-completions", "anthropic": "anthropic-messages"}  # a profile's API style in pi's words


def pi_provider(profile: Profile) -> dict[str, object]:
    """Build pi's provider entry from the catalog. The key is an environment variable reference."""
    return {"baseUrl": profile.base_url, "api": PI_APIS[profile.api], "apiKey": f"${profile.variable}",
            "models": [{"id": model.id, "reasoning": True, **dict(model.settings)} for model in profile.models]}


# Where each harness's provider file keeps its providers, and the entry of one profile there
PROVIDER_ENTRIES: dict[str, tuple[str, Callable[[str, Profile], dict[str, object]]]] = {
    "pi": ("providers", lambda _, profile: pi_provider(profile))}


# The local review's model and effort level: each profile's `review_model` and `review_effort` in the orchestrator's
# config, which `owlcode install` writes as this JSON file beside the installed copy (`restart` writes it again
# after an edit of the config). Here rather than in that config alone: the review's command goes into every environment that
# `keys_env` builds, and this script runs on Python 3.9, which reads no TOML.
REVIEW_FILE = "local-review.json"


def install_folder() -> Path:
    """The folder that the orchestrator's service runs from: the `WorkingDirectory` of its plist, which
    orchestrator/deploy/install.py writes."""
    try:
        loaded = plistlib.loads(ORCHESTRATOR_PLIST.read_bytes())
    except (OSError, ValueError) as e:
        raise Refused(f"cannot read {ORCHESTRATOR_PLIST} ({type(e).__name__}): run `owlcode install` as the "
                      "owner") from None
    folder = loaded.get("WorkingDirectory") if isinstance(loaded, dict) else None
    if not isinstance(folder, str) or not folder.startswith("/"):
        raise Refused(f"{ORCHESTRATOR_PLIST} names no WorkingDirectory: run `owlcode install` as the owner")
    return Path(folder)


def installed_file(name: str) -> Path:
    """A settings file that the install writes: the one beside this copy of the script when it is there (the service's own,
    and the staged copy that an install's dry round runs), else the one in the folder that the service runs from, whose plist
    names it (a copy in the agent's home has none beside it: `admin` writes the script alone)."""
    beside = HERE / name
    return beside if beside.exists() else install_folder() / name


def review_file() -> Path:
    """The local review's settings, `local-review.json`."""
    return installed_file(REVIEW_FILE)


# The profile in force (the owner's call of 2026-11-23: the config is the one place of the profile): the config's
# `inference_profile`, which `install` and `restart` write as this JSON file beside the installed copy. `paseo-daemon` reads
# it, so the daemon that the service starts holds the profile that the service runs. A file of its own, not a key of
# `local-review.json`: an older copy of this script, from before the inference profiles, refuses a key there that names no profile.
PROFILE_FILE = "inference-profile.json"


def profile_in_force() -> str:
    """The orchestrator's `inference_profile`, from `inference-profile.json`. A file that is missing, broken or names no
    profile of PROFILES stops the start: a daemon on a profile that nobody chose never starts."""
    path = installed_file(PROFILE_FILE)
    try:
        found = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise Refused(f"cannot read {path} ({type(e).__name__}): it holds the orchestrator's inference_profile, which "
                      "`owlcode restart` writes there: run it as the owner (or `owlcode install`)") from None
    name = found.get("inference_profile") if isinstance(found, dict) else None
    if not isinstance(name, str) or name not in PROFILES:
        raise Refused(f"{path} names no profile that this script knows ({name!r}): run `owlcode install` as the owner")
    return name


def review_json() -> tuple[Path, dict[str, object]]:
    """The settings file's path and its parsed object: a file that cannot be read, or is no object, stops the start here."""
    path = review_file()
    try:
        found = json.loads(path.read_text())
    except OSError as e:
        raise Refused(f"cannot read {path} ({e.strerror}): the local review's model and effort of each profile are the "
                      "orchestrator's config, which `owlcode restart` writes there: run it as the owner (or "
                      "`owlcode install`)") from None
    except ValueError as e:
        raise Refused(f"{path} is not JSON ({e}): run `owlcode restart` as the owner") from None
    if not isinstance(found, dict):
        raise Refused(f"{path} is not an object of profiles: run `owlcode restart` as the owner")
    return path, found


def review_settings() -> dict[str, tuple[str, str]]:
    """The local review's (model, effort) of each profile, which the orchestrator's config names: one model for
    each profile, whatever the issue's labels, so two reviews of one profile are comparable. A file that its `[profiles.NAME]`
    tables do not match stops the start here, never a review on a model that nobody chose."""
    path, found = review_json()
    read: dict[str, tuple[str, str]] = {}
    for name, table in found.items():
        if name not in PROFILES:
            raise Refused(f"{path} names the profile {name!r}, which this script does not know: run "
                          "`owlcode install` as the owner")
        values = [table.get(k) if isinstance(table, dict) else None for k in ("model", "effort")]
        if not all(isinstance(v, str) and SETTING.fullmatch(v) for v in values):
            raise Refused(f"{path}: [profiles.{name}] needs a model and an effort level: run `owlcode restart` "
                          "as the owner")
        read[name] = (values[0], values[1])
    if unknown := sorted(set(PROFILES) - set(read)):
        raise Refused(f"{path} names no local review for {', '.join(unknown)}: run `owlcode restart` as the owner")
    return read


def profile_set(name: str) -> list[str]:
    """The profile `name` first, then each other profile that its cells run on: the `cells` of its entry in the
    settings file, which `install` and `restart` write from the config. The first is the one whose review the Paseo daemon
    holds. A file from before the cells has no `cells`: the profile alone."""
    path, found = review_json()
    table = found.get(name)
    cells = table.get("cells", [name]) if isinstance(table, dict) else None
    if not isinstance(cells, list) or cells[:1] != [name] or len(set(cells)) != len(cells) or not all(c in PROFILES for c in cells):
        raise Refused(f"{path}: the cells of {name} name no list of profiles that starts with {name}: run "
                      "`owlcode restart` as the owner")
    return list(cells)


def review_digest(names: Sequence[str]) -> str:
    """A digest of the entries of `names` in the settings file: `start_paseo` records it, and the orchestrator
    computes the same one (`review_digest` there) to see a change of the review settings alone."""
    found = review_json()[1]
    return hashlib.sha256(json.dumps({name: found[name] for name in names}, sort_keys=True).encode()).hexdigest()


def review_command(profile: Profile, keychain: Path, review: tuple[str, str], name: str = "") -> str:
    """The local review's command (agents/README.md "The local review") on this profile and its `review` (the
    model and the effort level that the config names): every environment that `keys_env` builds (a session, `run`,
    the Paseo daemon) holds it as LOCAL_REVIEW_COMMAND, so no start of a session can lack it. It reads the profile's key into
    its own review command (a Paseo agent's shell may hold none), after the provider's variables. `$(...)` and
    `$LOCAL_REVIEW_SCHEMA` stay text until the review's shell runs it, so no command line and no record of Paseo holds the key."""
    model, effort = review
    key = (f"{profile.variable}=$(/usr/bin/security find-generic-password -a {USER} -s {SERVICE}.{profile.key} -w {keychain}) "
           if profile.key else "")  # a profile on the harness's own sign-in takes none
    # `{provider}` is the profile's name in the harness's provider file, or the harness's own provider for a catalog
    # provider, which has no entry there
    command = ("".join(f"{var}={shlex.quote(value)} " for var, value in provider_env(profile))
               + key
               + profile.review_cli.format(model=model, effort=effort, provider=profile.provider or name,
                                           budget=f" --max-budget-usd {profile.budget}" if profile.budget else ""))
    if profile.harness == "pi" and keychain.parent.parts[-2:] == ("Library", "Keychains"):
        extension = keychain.parents[2] / METRICS_EXTENSION
        if extension.is_file():  # Use the owner's installed copy, never an extension from the review worktree.
            command += f" --extension {shlex.quote(str(extension))}"
    return command


VALUE = re.compile(r"[A-Za-z0-9+/=._:-]+")  # what a Keychain value may hold: it goes into `security -i`'s command line
# A model or an effort level from the JSON file: it goes into a shell command too (as the orchestrator's SETTING_SHAPE has it)
SETTING = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/:@-]{0,63}")


class Refused(Exception):
    pass


class Sh:
    """The commands the script runs. Tests replace it; `out` captures, `tty` hands the terminal to the command."""

    def out(self, cmd: Sequence[str], stdin: str | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(list(cmd), input=stdin, capture_output=True, text=True, check=False, cwd=cwd)

    def tty(self, cmd: Sequence[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> int:
        return subprocess.run(list(cmd), check=False, cwd=cwd, env=env).returncode

    def must(self, cmd: Sequence[str], stdin: str | None = None) -> str:
        done = self.out(cmd, stdin)
        if done.returncode:
            raise Refused(f"{cmd[0]} {cmd[1] if len(cmd) > 1 else ''}: {(done.stderr or done.stdout).strip()}")
        return done.stdout

    def tty_must(self, cmd: Sequence[str], cwd: Path | None = None) -> None:
        if self.tty(cmd, cwd):
            raise Refused(f"{' '.join(cmd[:2])} failed: read its lines above")


class Ask:
    def plain(self, prompt: str) -> str:
        return input(prompt).strip()

    def secret(self, prompt: str) -> str:
        return getpass.getpass(prompt).strip()

    def pem(self, prompt: str) -> str:
        fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
        try:
            return read_pem(fd, prompt)
        finally:
            os.close(fd)


PEM = re.compile(r"-----BEGIN ([A-Z ]+)-----(.*?)-----END \1-----", re.S)


def read_pem(fd: int, prompt: str) -> str:
    """A pasted PEM private key, read from the terminal with no echo and no line limit.

    The terminal is in non-canonical mode: in canonical mode macOS ends a line at 1024 bytes (MAX_CANON) and then drops
    every key, Enter too, so a paste on one line waited with no end. The read ends at the PEM's END
    marker, at Enter when the paste has no BEGIN line, or at Ctrl-D. Ctrl-C still stops the script.
    """
    old = termios.tcgetattr(fd)
    quiet = termios.tcgetattr(fd)
    quiet[3] &= ~(termios.ECHO | termios.ICANON)
    quiet[6][termios.VMIN], quiet[6][termios.VTIME] = 1, 0
    termios.tcsetattr(fd, termios.TCSAFLUSH, quiet)  # before the prompt, as getpass does: a paste after it stays
    got = b""
    try:
        os.write(fd, prompt.encode())
        while len(got) < 16384:
            got += os.read(fd, 4096)
            text = got.decode("ascii", "replace")
            if "\x04" in text or PEM.search(text):
                break
            if "-----BEGIN" not in text and text.strip() and text.endswith(("\n", "\r")):
                break  # one line of base64, ended by Enter
    finally:
        termios.tcsetattr(fd, termios.TCSAFLUSH, old)  # what came after the end (an extra Enter) goes
        os.write(fd, b"\n")
    return pem_text(got.decode("ascii", "replace").replace("\x04", ""))


def pem_text(pasted: str) -> str:
    """The PEM file's text from a paste in any of its forms: with its lines, on one line, or as the base64 of the file.

    The body's whitespace goes and its lines are 64 characters again, as openssl and GitHub write them. A paste that is
    none of these comes back stripped, and the caller refuses it."""
    found = PEM.search(pasted)
    if not found:
        try:
            found = PEM.search(base64.b64decode("".join(pasted.split()), validate=True).decode("ascii"))
        except ValueError:  # binascii.Error and UnicodeDecodeError are ValueErrors
            return pasted.strip()
        if not found:
            return pasted.strip()
    label, body = found.group(1), "".join(found.group(2).split())
    lines = [body[i:i + 64] for i in range(0, len(body), 64)]
    return "\n".join([f"-----BEGIN {label}-----", *lines, f"-----END {label}-----"]) + "\n"


def member(sh: Sh, user: str, group: str) -> bool:
    return "is a member" in sh.out(["dsmemberutil", "checkmembership", "-U", user, "-G", group]).stdout


def closed(path: Path) -> bool:
    """No permission for the group or for others."""
    return path.stat().st_mode & 0o077 == 0


# ---- admin: run as root from the owner's checkout ----

def sshd_conf() -> str:
    """The agent's sshd rules: only it, from this Mac or the tailnet, only with a key, no forwarding but local forwarding."""
    return "\n".join([
        f"# {SCRIPT}: herdr reaches the agent {USER} over SSH from this Mac, Heeler from the tailnet",
        f"AllowUsers {' '.join(ALLOWED)}",
        "PasswordAuthentication no",
        "KbdInteractiveAuthentication no",
        f"Match User {USER}",
        "    AllowTcpForwarding local",  # OpenSSH checks it for a stream to a socket too (session.c: "XXX - streamlocal?")
        "    AllowStreamLocalForwarding local",
        "    PermitListen none",
        "    AllowAgentForwarding no",
        "    X11Forwarding no",
        "    PermitTunnel no",
    ]) + "\n"


def key_part(line: str) -> str:
    """An authorized_keys line without its options: the key's type, the key and its comment ("" for a comment line)."""
    found = re.search(r"(?:^|\s)((?:ssh-|ecdsa-|sk-)\S+\s+\S+.*)$", line.strip())
    return found.group(1) if found and not line.lstrip().startswith("#") else ""


def key_id(key: str) -> str:
    """A key's type and blob, without options or comment: what two lines of one key share."""
    return " ".join(key.split()[:2])


def fingerprint(key: str) -> str:
    """The SHA256 fingerprint `ssh-keygen -l` prints, for the owner to compare with the phone's."""
    try:
        blob = base64.b64decode(key.split()[1], validate=True)
    except (IndexError, ValueError):
        return "SHA256:?"
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")


def one_time(line: str) -> bool:
    """A pairing's one-time key (Heeler's Bootstrap Key: a forced command, its comment herdr-pairing:...). Its secret was
    in a QR code: it never becomes a login key, so `admin` drops it and `check` fails on it."""
    options = line.strip()[: -len(key_part(line))] if key_part(line) else line
    return "command=" in options or "herdr-pairing:" in line


def approved(phones: Path) -> set[str]:
    return {key_id(line) for line in phones.read_text().splitlines() if line.strip()} if phones.is_file() else set()


def host_block(owner_home: Path) -> str:
    return "\n".join([f"Host {USER}", "  HostName 127.0.0.1", f"  User {USER}",
                      f"  IdentityFile {owner_home / '.ssh' / SSH_KEY}", "  IdentitiesOnly yes", "  ForwardAgent no"])


def ssh_access(sh: Sh, owner: str, owner_home: Path, home: Path, sshd_dir: Path, ask: Ask, phones: Path) -> list[str]:
    """sshd's rules, the group in Remote Login, the owner's key and the agent's authorized_keys. Returns notes."""
    conf = sshd_dir / f"000-{USER}.conf"
    before = conf.read_text() if conf.exists() else None
    if before != sshd_conf():
        conf.write_text(sshd_conf())
        conf.chmod(0o644)
        tested = sh.out(["sshd", "-t"])
        if tested.returncode:  # a broken sshd_config would refuse every login: put the old one back
            if before is None:
                conf.unlink()
            else:
                conf.write_text(before)
            said = (tested.stderr or tested.stdout).strip().splitlines()[-1:]  # No host keys, for one
            raise Refused(f"sshd -t refused the config with {conf} ({said[0] if said else 'no message'}): nothing changed in sshd")
    if not sh.out(["dscl", ".", "-read", "/Groups/com.apple.access_ssh"]).returncode:  # Remote Login for "Only these users"
        sh.out(["dseditgroup", "-o", "edit", "-a", GROUP, "-t", "group", "com.apple.access_ssh"])
    key = owner_home / ".ssh" / SSH_KEY
    as_owner = ["sudo", "-u", owner, "-H"]
    if sh.out([*as_owner, "test", "-f", f"{key}.pub"]).returncode:
        sh.must([*as_owner, "mkdir", "-p", "-m", "700", str(owner_home / ".ssh")])
        # no passphrase: herdr reconnects in the background and cannot answer a prompt; the key opens only the agent
        sh.must([*as_owner, "ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", SSH_KEY, "-f", str(key)])
    public = sh.must([*as_owner, "cat", f"{key}.pub"]).strip()
    as_agent = ["sudo", "-u", USER, "-H"]
    sh.must([*as_agent, "mkdir", "-p", "-m", "700", str(home / ".ssh")])
    keys = home / ".ssh" / "authorized_keys"
    had = sh.out([*as_agent, "cat", str(keys)])  # as the agent: root names no path inside the agent's home but the home
    yes, kept, dropped = approved(phones), [], []
    for raw in had.stdout.splitlines() if not had.returncode else []:
        key = key_part(raw)
        if not key or key_id(key) == key_id(public) or any(key_id(key) == key_id(k) for k in kept):
            continue
        if one_time(raw):
            dropped.append(f"{fingerprint(key)} (a pairing's one-time key)")
            continue
        if key_id(key) not in yes:  # the agent owns the file, so a key it added waits for the owner's yes
            comment = " ".join(key.split()[2:]) or "no comment"
            said = ask.plain(f"A key in the agent's authorized_keys that you have not approved: {key.split()[0]} "
                             f"{fingerprint(key)} ({comment}). Let it log in as {USER} from the tailnet (Heeler)? [y/N] ")
            if said.lower() not in ("y", "yes"):
                dropped.append(f"{fingerprint(key)} ({comment})")
                continue
            yes.add(key_id(key))
            phones.write_text("".join(f"{k}\n" for k in sorted(yes)))
            phones.chmod(0o644)
        kept.append(key)
    if phones.is_file() and approved(phones) != {key_id(k) for k in kept}:  # A removed phone is forgotten
        phones.write_text("".join(f"{key_id(k)}\n" for k in sorted(kept)))
    text = "".join(f"{options} {k}\n" for options, k in [(KEY_OPTIONS, public), *((PHONE_OPTIONS, k) for k in kept)])
    sh.must([*as_agent, "/usr/bin/tee", str(keys)], stdin=text)
    sh.must([*as_agent, "chmod", "600", str(home / ".ssh" / "authorized_keys")])
    notes = [*(f"Removed from the agent's authorized_keys: {d}" for d in dropped),
             f"Put this block at the top of your ~/.ssh/config (ssh keeps the first value it reads):\n{host_block(owner_home)}",
             f"Then, once: herdr machine add {USER} --label {USER} --remote-session default"]
    if re.search(r'"com\.openssh\.sshd" => (disabled|true)', sh.out(["launchctl", "print-disabled", "system"]).stdout):
        notes.insert(0, f"Remote Login is off: System Settings, General, Sharing, Remote Login on, for {GROUP!r} (Coding agents) only.")
    return notes


def paseo_item(orchestrator_user: str, team: str) -> str:
    """The orchestrator's keychain item that holds the password of the Paseo daemon of `team`'s user: the item prefix of
    the orchestrator's own keys (`USER-agent.`), then `TEAM-paseo-password` in lower case."""
    return f"{orchestrator_user}-agent.{team.lower()}-paseo-password"


def paseo_notes(sh: Sh, project: Project, orchestrator_user: str) -> list[str]:
    """What the orchestrator's user needs to reach the project's Paseo daemon: the config's two lines, and the step that
    stores the daemon's password in its keychain at a hidden prompt. Never the password: Paseo keeps only its hash."""
    address = tailnet_address(sh)
    host = f"{address or 'ADDRESS'}:{project.paseo_port}"
    item = paseo_item(orchestrator_user, project.team)
    try:
        keychain = Path(pwd.getpwnam(orchestrator_user).pw_dir) / "Library" / "Keychains" / KEYCHAIN
    except KeyError:
        keychain = Path("/Users") / orchestrator_user / "Library" / "Keychains" / KEYCHAIN
    return [
        *([] if address else ["Tailscale is not connected: ADDRESS below is this Mac's Tailscale address (`tailscale ip -4`), "
                              "on which Paseo's daemon listens."]),
        f"Paseo: the daemon of {USER} listens on {host} (`paseo-daemon` sets it at each start). `user` asks once for the "
        "daemon's own password: keep it in your password manager.",
        f"In the orchestrator's config, the table of team {project.team}:\n  paseo_host = \"{host}\"\n"
        f"  paseo_password_item = \"{item}\"",
        f"After `user`, store the same password in the keychain of the orchestrator's user {orchestrator_user} (the second "
        "command asks for it at a hidden prompt):\n"
        f"  sudo -u {orchestrator_user} -H security unlock-keychain {keychain}\n"
        f"  sudo -u {orchestrator_user} -H security add-generic-password -U -a {orchestrator_user} -s {item} -w",
    ]


def admin(sh: Sh, owner: str, owner_home: Path, home: Path, script_text: str, sshd_dir: Path = SSHD_DIR,
          ask: Ask | None = None, project: Project | None = None, orchestrator_user: str = "") -> list[str]:
    """Make the group, the user and its home. Returns the notes to print. When the macOS user does not exist, it asks first
    and changes nothing unless the answer is `y`. With a `project` (`owlcode admin --project TEAM`), it writes that
    project's file in the home and prints what the orchestrator's user needs for the user's Paseo daemon."""
    new_user = bool(sh.out(["dscl", ".", "-read", f"/Users/{USER}"]).returncode)
    if new_user:  # before any change: the name can be wrong (the checkout's `origin` is not the service's agents' user)
        source = ("AGENT_USER" if os.environ.get("AGENT_USER") else "the checkout's `origin`") if not project else (
            f"the agent_user of team {project.team} in the installed config")
        answer = (ask or Ask()).plain(f"No macOS user {USER} exists. Make it (a new agents' user, with its password, home and SSH "
                                      f"access)? The name came from {source} (`owlcode admin` sets AGENT_USER from [service] user of the "
                                      "installed config, `owlcode admin --project TEAM` from that team's agent_user). [y/N] ")
        if answer.strip().lower() != "y":
            raise Refused(f"no macOS user {USER} was made. For an existing agents' user, set [service] user of "
                      "/usr/local/etc/orchestrator.toml to its name (or the team's agent_user), then run owlcode admin again")
    if sh.out(["dscl", ".", "-read", f"/Groups/{GROUP}"]).returncode:
        taken = sh.out(["dscl", ".", "-search", "/Groups", "PrimaryGroupID", str(GID)]).stdout.split()
        if taken:
            raise Refused(f"the gid {GID} belongs to the group {taken[0]}: change GID in this script")
        sh.must(["dseditgroup", "-o", "create", "-i", str(GID), "-r", "Coding agents", GROUP])
    if new_user:
        print(f"The new user {USER} needs a password: type it twice, and keep it in Bitwarden.")
        sh.tty_must(["sysadminctl", "-addUser", USER, "-fullName", FULL_NAME, "-password", "-"])
    if member(sh, USER, "admin"):
        raise Refused(f"{USER} is an admin: an agent must not be (System Settings, Users & Groups)")
    sh.must(["dscl", ".", "-create", f"/Users/{USER}", "PrimaryGroupID", str(GID)])
    if not member(sh, USER, GROUP):
        sh.must(["dseditgroup", "-o", "edit", "-a", USER, "-t", "user", GROUP])
    if member(sh, USER, "staff"):
        sh.out(["dseditgroup", "-o", "edit", "-d", USER, "-t", "user", "staff"])  # `check` says when it stays
    sh.must(["dscl", ".", "-create", f"/Users/{USER}", "IsHidden", "1"])
    fresh = new_user or not home.exists()
    if not home.exists():
        sh.must(["createhomedir", "-c", "-u", USER])
    # Root never writes through a path inside the agent's home: once the agent has run, a file there can
    # be its symlink to /etc or to the owner's home. Only a home made in this run is changed as a whole; after that, root
    # touches the folder itself, and every file in it is written as the agent, so a symlink reaches only what it can write.
    sh.must(["chown", "-R" if fresh else "-h", f"{USER}:{GROUP}", str(home)])
    sh.must(["chmod", "700", str(home)])
    as_agent = ["sudo", "-u", USER, "-H"]
    sh.must([*as_agent, "/usr/bin/tee", str(home / CATALOG_FILE)], stdin=CATALOG_PATH.read_text())
    sh.must([*as_agent, "/usr/bin/tee", str(home / METRICS_EXTENSION)], stdin=METRICS_SOURCE.read_text())
    sh.must([*as_agent, "/usr/bin/tee", str(home / SCRIPT)], stdin=script_text)
    sh.must([*as_agent, "chmod", "700", str(home / SCRIPT)])
    if project:  # written as the agent, as every file in its home
        sh.must([*as_agent, "/usr/bin/tee", str(home / PROJECT_FILE)], stdin=json.dumps(project._asdict(), indent=2) + "\n")
    for key in ("user.name", "user.email"):  # the git author is the owner (AGENTS.md "Identity"); the push is the app's
        value = sh.out(["sudo", "-u", owner, "git", "config", "--global", key]).stdout.strip()
        if value:  # -C: the owner's checkout, the working folder, is closed to the agent
            sh.must([*as_agent, "git", "-C", str(home), "config", "--file", str(home / ".gitconfig"), key, value])
    notes = [*ssh_access(sh, owner, owner_home, home, sshd_dir, ask or Ask(),
                                    sshd_dir.parent / PHONES.name),
             *(paseo_notes(sh, project, orchestrator_user) if project and project.paseo_port else []),
             f"{USER} is ready{f' for team {project.team} ({project.repo})' if project else ''}. Next, as the agent: "
             f"sudo -iu {USER} /usr/bin/python3 -I {SCRIPT} user"]
    if not closed(owner_home):
        notes.append(f"Your home folder {owner_home} is open to other users: run `chmod 700 ~` (no sudo).")
    return notes


# ---- the Keychain ----

# The agent's own keychain, named by its full path in every `security` call. A keychain named "login" that the
# agent makes from a `sudo -iu` session refuses the password it was made with (the owner's probes, 2026-11-03:
# `probe.keychain-db` unlocked, `login.keychain-db` in another folder did not), and nothing here needs a login keychain.
KEYCHAIN = "agent.keychain-db"


def keychain_file(home: Path | None = None) -> str:
    return str((home or Path.home()) / "Library" / "Keychains" / KEYCHAIN)


def keychain_get(sh: Sh, name: str) -> str | None:
    done = sh.out(["security", "find-generic-password", "-a", USER, "-s", f"{SERVICE}.{name}", "-w", keychain_file()])
    if done.returncode:
        return None
    return done.stdout.strip() or None


def keychain_set(sh: Sh, name: str, value: str) -> None:
    """The value goes to `security -i` on its standard input, never on a command line that `ps` shows."""
    if not VALUE.fullmatch(value):
        raise Refused(f"the value for {name} has a character the Keychain step does not take")
    sh.must(["security", "-i"], stdin=f"add-generic-password -U -a {USER} -s {SERVICE}.{name} -w {value} {keychain_file()}\n")


def ensure_keychain(sh: Sh, home: Path) -> list[str]:
    """Make the agent's keychain when it has none, or unlock it; then it never locks by itself. Returns notes.

    The search list and the default name it too, for the tools that use the default (the Claude CLI). A new keychain
    locks after 300 s and on sleep, and `set-keychain-settings` on a locked keychain waits with no end: so the settings
    come right after the create or the unlock. `unlock-keychain` asks for the password even when the keychain is open.
    `security login-keychain -s` failed with no message in a `sudo -iu` session: nothing calls it.
    """
    keychain = keychain_file(home)
    if Path(keychain).exists():
        print("Unlock the agent's keychain: type its password.")
        sh.tty_must(["security", "unlock-keychain", keychain])
    else:
        print("The agent has no keychain yet: give it a password, and keep it in Bitwarden with the user's.")
        sh.tty_must(["security", "create-keychain", keychain])
    sh.must(["security", "list-keychains", "-d", "user", "-s", keychain])
    sh.must(["security", "default-keychain", "-d", "user", "-s", keychain])
    sh.must(["security", "set-keychain-settings", keychain])  # no timeout, no lock on sleep
    old = home / "Library" / "Keychains" / "login.keychain-db"
    if old.exists():
        return [f"The keychain {old} that an older version of this script made is not used now. "
                f"Delete it as {USER}: security delete-keychain {old}"]
    return []


def unlock(sh: Sh) -> None:
    """A `sudo -iu` session after a restart finds the keychain locked: ask for its password once."""
    if keychain_get(sh, "linear-key") is None:
        sh.tty(["security", "unlock-keychain", keychain_file()])


def ask_keys(sh: Sh, ask: Ask, replace: bool) -> None:
    for name, prompt, hidden, shape in KEYS:
        if keychain_get(sh, name) and not replace:
            continue
        for harness in HARNESSES.values():  # a key that a harness's CLI makes: its command first
            for item, setup, said in harness.key_setup:
                if item == name:
                    print(said)
                    sh.tty([str(Path.home() / ".local" / "bin" / harness.binary), *setup])
        if name == "app-key":
            pem = ask.pem(prompt)
            if not (pem.startswith("-----BEGIN") and "PRIVATE KEY-----" in pem and pem.rstrip().endswith("-----")):
                raise Refused("that is not a PEM private key from BEGIN to END (nothing was stored): run `user` again")
            value = base64.b64encode(pem.encode()).decode()  # the Keychain value is one line
        else:
            value = (ask.secret if hidden else ask.plain)(prompt)
        if name in OPTIONAL_KEYS and not value.strip():
            print(f"{name}: skipped (a stored one stays; `user` asks again next time)")
            continue
        if not re.fullmatch(shape, value):
            raise Refused(f"that does not look like the {name} (nothing was stored): run `user` again")
        keychain_set(sh, name, value)
        print(f"{name}: stored in the Keychain")


# ---- the app's token ----

def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def sign(data: bytes, key_pem: bytes) -> bytes:
    """RS256 with openssl: the key goes on its standard input, the data in a temporary file (it holds no secret)."""
    with tempfile.NamedTemporaryFile() as f:
        f.write(data)
        f.flush()
        done = subprocess.run(["openssl", "dgst", "-sha256", "-sign", "/dev/stdin", f.name], input=key_pem,
                              capture_output=True, check=False)
    if done.returncode:
        raise Refused("openssl could not sign with the app's key: store it again (`user --replace`)")
    return done.stdout


def app_jwt(client_id: str, key_pem: bytes, now: float) -> str:
    head = b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    body = b64url(json.dumps({"iat": int(now) - 60, "exp": int(now) + 540, "iss": client_id}).encode())
    return f"{head}.{body}.{b64url(sign(f'{head}.{body}'.encode(), key_pem))}"


Fetch = Callable[..., "dict[str, object]"]  # (method, path, bearer) and, for a request with a body, the body


def fetch(method: str, path: str, bearer: str, body: dict[str, object] | None = None) -> dict[str, object]:
    req = urllib.request.Request(API + path, method=method, data=None if body is None else json.dumps(body).encode(), headers={
        "Accept": "application/vnd.github+json", "Authorization": f"Bearer {bearer}", "User-Agent": SERVICE,
        **({"Content-Type": "application/json"} if body is not None else {})})
    with urllib.request.urlopen(req, timeout=30) as reply:
        data: dict[str, object] = json.load(reply)
    return data


def app_token(sh: Sh, api: Fetch = fetch, now: Callable[[], float] = time.time) -> str:
    """The app's installation token, from the Keychain while it has more than five minutes left, else a new one. A
    project's own user (PROJECT) asks for a token of its repository only (`repositories`), so the token reaches no other
    repository that the app is installed on."""
    cached = keychain_get(sh, "gh-token")
    if cached and ":" in cached:
        ends, token = cached.split(":", 1)
        if int(ends) - now() > 300:
            return token
    client_id, key = keychain_get(sh, "app-client-id"), keychain_get(sh, "app-key")
    if not client_id or not key:
        raise Refused("the app's keys are not in the Keychain: run `user` (or unlock its keychain: `security unlock-keychain ~/Library/Keychains/agent.keychain-db`)")
    jwt = app_jwt(client_id, base64.b64decode(key), now())
    installation = api("GET", f"/repos/{REPO}/installation", jwt)["id"]
    tokens = f"/app/installations/{installation}/access_tokens"
    reply = api("POST", tokens, jwt, {"repositories": [project_name(PROJECT.repo)]}) if PROJECT else api("POST", tokens, jwt)
    token, ends_at = str(reply["token"]), str(reply["expires_at"])
    keychain_set(sh, "gh-token", f"{calendar.timegm(time.strptime(ends_at, '%Y-%m-%dT%H:%M:%SZ'))}:{token}")
    return token


# The orchestrator's own GitHub App (OWL-70, the owner's call of 2026-11-26, design b2): the service runs in a user of
# its own, which holds no implementer key. `[service] app_client_id` and `[service] app_key_item` of the config name the
# app's client ID and the keychain item of its private key; `install` and `restart` write them, with each product's
# repository, as this JSON file beside the installed copy. Only the installed copy reads it (never a copy in an agent's
# home, which has none beside it): the service's `gh` then mints the token with that app. With no file, or with the two
# keys empty, the shim works as before.
APP_FILE = "service-app.json"
APP_CLIENT_ID = re.compile(r"Iv[0-9A-Za-z.]{8,40}")  # KEYS' shape of an app's client ID
APP_KEY_ITEM = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@:-]{0,127}")  # a keychain item's name: it goes on a command line
APP_REPO = re.compile(r"[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}")  # OWNER/NAME
REPO_PATH = re.compile(r"/?repos/([A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100})(?:[/?].*)?")  # `gh api repos/OWNER/NAME/...`


class ServiceApp(NamedTuple):
    client_id: str
    key_item: str
    repos: tuple[str, ...]  # each product's repository, the first product's first


def service_app(here: Path | None = None) -> ServiceApp | None:
    """The orchestrator's app from APP_FILE beside this copy; None when there is no file or its two keys are empty. A file
    that is broken or names a value of the wrong shape stops the call: the service must not fall back to another key."""
    path = (here or HERE) / APP_FILE
    if not path.is_file():
        return None
    try:
        found = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise Refused(f"cannot read {path} ({type(e).__name__}): run `owlcode restart` as the owner") from None
    if not isinstance(found, dict):
        raise Refused(f"{path} is not a JSON object: run `owlcode restart` as the owner")
    client_id, item, repos = found.get("client_id") or "", found.get("key_item") or "", found.get("repos") or []
    if not client_id and not item:
        return None
    if not isinstance(client_id, str) or not APP_CLIENT_ID.fullmatch(client_id):
        raise Refused(f"{path}: client_id {client_id!r} is not an app's client ID: set [service] app_client_id, then `owlcode restart`")
    if not isinstance(item, str) or not APP_KEY_ITEM.fullmatch(item):
        raise Refused(f"{path}: key_item {item!r} is not a keychain item's name: set [service] app_key_item, then `owlcode restart`")
    if not isinstance(repos, list) or not repos or not all(isinstance(r, str) and APP_REPO.fullmatch(r) for r in repos):
        raise Refused(f"{path}: repos must list each product's repository as OWNER/NAME: run `owlcode restart`")
    return ServiceApp(client_id, item, tuple(repos))


def call_repo(args: Sequence[str], environ: Mapping[str, str], default: str) -> str:
    """The repository of one `gh` call: its `--repo` (`-R`, `--repo=`), else the `repos/OWNER/NAME` path of `gh api`,
    else GH_REPO, else `default` (the first product's repository: `gh api /installation/repositories` names none)."""
    named = ""
    for at, arg in enumerate(args):
        if arg in ("--repo", "-R") and at + 1 < len(args):
            named = args[at + 1]
            break
        if arg.startswith("--repo="):
            named = arg.split("=", 1)[1]
            break
    else:
        if args[:1] == ["api"]:
            named = next((found.group(1) for arg in args[1:] if (found := REPO_PATH.fullmatch(arg))), "")
    named = named or environ.get("GH_REPO", "") or default
    named = "/".join(named.split("/")[-2:])  # HOST/OWNER/NAME, as gh takes it, is OWNER/NAME on github.com
    if not APP_REPO.fullmatch(named):
        raise Refused(f"gh: {named!r} is not a repository as OWNER/NAME")
    return named


def app_key_pem(sh: Sh, item: str) -> bytes:
    """The orchestrator's app's private key from the keychain item `item` of this user's keychain: the PEM itself, or the
    PEM as one line of base64 (as `user` stores `app-key`). The value goes to no output and no file."""
    done = sh.out(["security", "find-generic-password", "-s", item, "-w", keychain_file()])
    value = done.stdout.strip() if done.returncode == 0 else ""
    if not value:
        raise Refused(f"the orchestrator's app key is not in the keychain item {item} (security exited {done.returncode}): "
                      "store it as agents/README.md \"Setting up a product\" says, or unlock the keychain (`owlcode unlock`)")
    if value.startswith("-----BEGIN"):
        return value.encode()
    try:
        return base64.b64decode(value, validate=True)
    except ValueError:
        raise Refused(f"the keychain item {item} holds no PEM private key: store it again") from None


def service_app_token(sh: Sh, app: ServiceApp, repo: str, api: Fetch = fetch, now: Callable[[], float] = time.time) -> str:
    """An installation token of the orchestrator's app for `repo`: from the keychain while it has more than five minutes
    left, else a new one. Each repository has its own cached token (`app-token.OWNER.NAME`): products of several owners
    have several installations. The installation comes from `/repos/OWNER/NAME/installation`."""
    item = "app-token." + repo.lower().replace("/", ".")
    cached = keychain_get(sh, item)
    if cached and ":" in cached:
        ends, token = cached.split(":", 1)
        if ends.isdigit() and int(ends) - now() > 300:
            return token
    jwt = app_jwt(app.client_id, app_key_pem(sh, app.key_item), now())
    try:
        installation = api("GET", f"/repos/{repo}/installation", jwt)["id"]
        reply = api("POST", f"/app/installations/{installation}/access_tokens", jwt)
    except urllib.error.HTTPError as e:
        e.close()
        raise Refused(f"the orchestrator's app is not installed on {repo}, or its key is not the app's (GitHub answered "
                      f"{e.code}): `owlcode check` shows each repository") from None
    token, ends_at = str(reply["token"]), str(reply["expires_at"])
    keychain_set(sh, item, f"{calendar.timegm(time.strptime(ends_at, '%Y-%m-%dT%H:%M:%SZ'))}:{token}")
    return token


def app_check(sh: Sh, app: ServiceApp, api: Fetch = fetch, now: Callable[[], float] = time.time) -> list[tuple[str, str]]:
    """For `owlcode check`: whether the orchestrator's app reaches each product's repository, one line each. The app's
    name is its slug (`GET /app`). No token is minted and no value is printed."""
    try:
        jwt = app_jwt(app.client_id, app_key_pem(sh, app.key_item), now())
        name = str(api("GET", "/app", jwt).get("slug") or app.client_id)
    except Refused as e:
        return [(FAIL, f"github: the orchestrator's app: {e}")]
    except (urllib.error.URLError, OSError, ValueError) as e:
        return [(FAIL, f"github: the orchestrator's app {app.client_id} does not answer: GitHub refused its key "
                       f"({getattr(e, 'code', type(e).__name__)}): check app_client_id and the key item {app.key_item}")]
    lines = []
    for repo in app.repos:
        try:
            api("GET", f"/repos/{repo}/installation", jwt)
        except urllib.error.HTTPError as e:
            e.close()
            lines.append((FAIL, f"github: the orchestrator's app {name} is not installed on {repo}" + ("" if e.code == 404 else f" (GitHub answered {e.code})")))
        except (urllib.error.URLError, OSError) as e:
            lines.append((FAIL, f"github: cannot ask GitHub whether the orchestrator's app {name} reaches {repo} ({type(e).__name__})"))
        else:
            lines.append((OK, f"github: the orchestrator's app {name} reaches {repo}"))
    return lines


def git_credential(sh: Sh, action: str, request: str) -> str:
    """git's credential helper protocol: answer `get` for github.com with the app's token; store and erase do nothing."""
    fields = dict(line.split("=", 1) for line in request.splitlines() if "=" in line)
    if action != "get" or fields.get("host") != "github.com":
        return ""
    return f"username=x-access-token\npassword={app_token(sh)}\n"


HERE = Path(__file__).resolve().parent


def shim_folders(home: Path, here: Path | None = None) -> list[Path]:
    """The folders that hold a gh shim, in PATH's order: the `bin` beside this script when it holds a gh (the orchestrator's
    installed copy, which root owns), then the agent's ~/.local/bin (the agent's shim, herdr, mise). The service
    so runs the installed copy's gh shim, never a file that the agent can write; herdr stays in ~/.local/bin."""
    own = (here or HERE) / "bin"
    return ([own] if os.path.isfile(own / "gh") else []) + [home / ".local" / "bin"]


def real_gh(sh: Sh) -> str:
    """gh on PATH (the shim folders skipped: a shim found there would run itself again), else the agent's own through its mise.

    Under `check`, sudo keeps the owner's PATH, whose folders are in the owner's home (700): `os.path.isfile` is False
    for a folder the agent cannot read, where Python 3.9's `Path.is_file` raises. mise runs in the agent's
    home, not in the folder the command came from, which the agent may not read either.
    """
    shims = shim_folders(Path.home())
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        if folder and Path(folder) not in shims and os.path.isfile(os.path.join(folder, "gh")):
            return os.path.join(folder, "gh")
    found = sh.out([str(Path.home() / ".local" / "bin" / "mise"), "which", "gh"], cwd=Path.home()).stdout.strip()
    if found and os.path.isfile(found):
        return found
    raise Refused(f"gh is not installed for {USER}: run `user` again (it installs github-cli {GH_VERSION} with mise)")

# ---- user: run as the agent ----

def append_once(path: Path, line: str) -> None:
    text = path.read_text() if path.exists() else ""
    if line not in text.splitlines():
        path.write_text(text + ("" if not text or text.endswith("\n") else "\n") + line + "\n")


def write_harness_settings(home: Path, harness: Harness) -> None:
    """A harness's own settings that `user` writes (its HARNESSES entry): only the entry's
    keys are set, every other setting stays as it is, and a file that holds them already is not written again."""
    if harness.settings_file and harness.settings:
        write_json(home / harness.settings_file, dict(harness.settings))


def harness_files(home: Path, name: str) -> list[tuple[Path, str, dict[str, object]]]:
    """The other JSON files that `user` writes for the harness `name`: (the file, the object that the keys go
    under or "", the keys). Its trust file marks each clone of loop_repos() trusted; its provider file gets each profile of
    the harness as a provider, under the profile's name, except a profile on a provider that the harness already has
    (`provider`): an entry under that name would replace the harness's own metadata for its models."""
    harness = HARNESSES[name]
    files: list[tuple[Path, str, dict[str, object]]] = []
    if harness.trust_file:
        files.append((home / harness.trust_file, "", {str(home / project_name(repo)): True for repo in loop_repos(clone_agents_md(home))}))
    if harness.provider_file:
        # The catalog includes the review models. A built-in provider keeps its own entry.
        under, entry = PROVIDER_ENTRIES[name]
        mine = {profile: p for profile, p in PROFILES.items() if p.harness == name}
        files.append((home / harness.provider_file, under,
                      {profile: entry(profile, p) for profile, p in mine.items() if not p.provider}))
    return files


def write_json(path: Path, keys: dict[str, object], under: str = "") -> None:
    """Set these keys in the JSON object of `path` (in its object `under`, when named): every other key stays as it is,
    and a file that holds them already is not written again. A file that is not a JSON object is refused, never
    overwritten: the owner fixes it."""
    try:
        settings = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError) as e:
        raise Refused(f"cannot read {path} as JSON ({type(e).__name__}): fix or move it, then run `user` again") from None
    target = settings.setdefault(under, {}) if under and isinstance(settings, dict) else settings
    if not isinstance(settings, dict) or not isinstance(target, dict):
        raise Refused(f"{path} is not a JSON object{f' with an object {under}' if under else ''}: fix or move it, then run `user` again")
    if all(key in target and type(target[key]) is type(value) and target[key] == value for key, value in keys.items()):
        return
    target.update(keys)
    replace_json(path, settings)


def replace_json(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = path.with_name(f".{path.name}.new")
    new.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(new, path)  # one step: a session that reads the file never finds half of it


GH_SHIM = f"""#!/bin/sh
# gh with the implementer app's token, made when it is needed (agent_user.py)
exec /usr/bin/python3 -I "$HOME/{SCRIPT}" gh "$@"
"""


def user(sh: Sh, ask: Ask, home: Path, name: str, replace: bool) -> list[str]:
    if name != USER:
        raise Refused(f"run this as {USER}: sudo -iu {USER} /usr/bin/python3 -I {SCRIPT} user")
    bin_ = home / ".local" / "bin"
    append_once(home / ".zprofile", "umask 077")  # what the agent writes to /tmp is its own
    append_once(home / ".zprofile", 'export PATH="$HOME/.local/bin:$PATH"')
    append_once(home / ".zshrc", 'eval "$("$HOME/.local/bin/mise" activate zsh)"')
    append_once(home / ".zshrc", CLAUDE_ALIAS)
    append_once(home / ".zshrc", AGENT_ALIAS)
    append_once(home / ".zshrc", PI_ALIAS)
    (home / ".zshenv").write_text(ZSHENV)  # the whole file: an older line held the owner's PATH
    if not (bin_ / "mise").exists():
        sh.tty_must(["sh", "-c", f"curl -fsSL https://mise.run | MISE_VERSION={MISE_VERSION} sh"])
    if (bin_ / "opencode").exists():  # OpenCode was dropped: remove what an earlier `user` installed
        sh.must(["sh", "-c", f'"$HOME/.local/bin/mise" exec node@{NODE_VERSION} -- npm uninstall -g --prefix "$HOME/.local" '
                            f"{OPENCODE_PACKAGE}"])  # a failure stops `user` here, with the config still in place
    shutil.rmtree(home / OPENCODE_CONFIG_DIR, ignore_errors=True)
    for harness in HARNESSES.values():  # each harness's CLI at its version, and its own settings
        binary = bin_ / harness.binary
        if not binary.exists() or (not harness.updates_itself and installed_version(sh, binary) != harness.version):
            sh.tty_must(["sh", "-c", harness.install.format(version=harness.version)])
        write_harness_settings(home, harness)
    notes = ensure_keychain(sh, home)
    if PROJECT:  # the project's own items only: this user's keychain holds no other project's key
        print(f"The keys of team {PROJECT.team} ({PROJECT.repo}) only: the implementer app installed on {PROJECT.repo} alone, a "
              f"Linear key limited to the team {PROJECT.team}, and a Claude token of this user's own.")
    ask_keys(sh, ask, replace)
    bin_.mkdir(parents=True, exist_ok=True)
    (bin_ / "gh").write_text(GH_SHIM)
    (bin_ / "gh").chmod(0o700)
    helper = f"!/usr/bin/python3 -I {home / SCRIPT} git-credential"
    # An empty helper first clears the list (gitcredentials(7)), so the system's osxkeychain never keeps an expired token
    sh.must(["git", "config", "--global", "--replace-all", "credential.https://github.com.helper", ""])
    sh.must(["git", "config", "--global", "--add", "credential.https://github.com.helper", helper])
    clone = home / CLONE
    if not clone.exists():
        clone.parent.mkdir(parents=True, exist_ok=True)
        sh.tty_must(["git", "clone", f"https://github.com/{REPO}.git", str(clone)])
    # the other JSON files after the clone: the trust file takes the loop's repositories from the clone's AGENTS.md
    for harness_name in HARNESSES:
        for path, under, keys in harness_files(home, harness_name):
            write_json(path, keys, under)
    mise = str(bin_ / "mise")
    for step in (["trust"], ["install"], ["use", "-g", f"github-cli@{GH_VERSION}"],
                 # herdr is its own install in ~/.local/bin: `unuse` only edits the config, so `prune` deletes the old
                 # install and `reshim` its shim (measured, mise 2026.10.10); both do nothing when there is none
                 ["unuse", "-g", "herdr"], ["prune", "-y", "herdr"],
                 ["use", "-g", f"node@{NODE_VERSION}"],  # Heeler's herdr plugin
                 ["reshim"], ["run", "hooks"], ["run", "start:deps"]):  # reshim: the shims are the agent's own mise
        sh.tty_must([mise, *step], cwd=clone)
    notes += paseo_link(sh, home, clone)
    if PROJECT:
        notes += paseo_password(sh, home)
    for profile_name, profile in PROFILES.items():  # a profile on the harness's own sign-in: the owner signs in
        if profile.provider:
            notes.append(f"The profile {profile_name} runs on pi's own provider {profile.provider}, which signs itself in: as {USER}, "
                         f"run pi and `/login` (and `pi-signin --provider {profile.provider}` says whether it is there)")
    return [*notes, f"Done. A session: sudo -iu {USER} /usr/bin/python3 -I {SCRIPT} session",
            f"Then, from your account: sudo /usr/bin/python3 -I .workflow/approved_run.py .workflow/{SCRIPT} -- check"]


def paseo_link(sh: Sh, home: Path, clone: Path, app: Path | None = None) -> list[str]:
    """~/.local/bin/paseo links to the Paseo app's CLI, and an npm copy in the agent's mise Node goes. With no
    app, nothing changes."""
    app = app or PASEO_APP
    if not app.is_file():
        return [f"No Paseo app at {app}: no paseo link. Install the app in your account, then run `user` again"]
    mise = str(home / ".local" / "bin" / "mise")
    npm = [mise, "exec", "--", "npm"]
    notes = []
    if sh.out([*npm, "ls", "-g", "--depth=0", PASEO_NPM], cwd=clone).returncode == 0:  # npm ls exits 1 when it is not there
        sh.tty_must([*npm, "uninstall", "-g", PASEO_NPM], cwd=clone)
        sh.tty_must([mise, "reshim"], cwd=clone)  # the npm copy's shim goes too
        notes.append(f"Removed the npm copy of Paseo ({PASEO_NPM}): paseo is the app's CLI now")
    link = home / ".local" / "bin" / "paseo"
    if not (link.is_symlink() and os.readlink(link) == str(app)):
        new = link.with_name(".paseo.new")
        if new.is_symlink() or new.exists():
            new.unlink()
        new.symlink_to(app)
        os.replace(new, link)  # one step: a run that reads the link never finds it missing
    return notes


def paseo_password(sh: Sh, home: Path, app: Path | None = None) -> list[str]:
    """A project's own Paseo daemon has its own password (design b2): when Paseo's config holds none, the owner types a
    new one at Paseo's own hidden prompt. Paseo keeps only its hash in ~/.paseo, and `config get` says only whether it is
    set, so this never reads the password. With no Paseo app, nothing changes."""
    if not (app or PASEO_APP).is_file():
        return []
    cli = str(home / ".local" / "bin" / "paseo")
    said = sh.out([cli, "daemon", "config", "get", "daemon.auth.password"], cwd=home)
    try:
        found = json.loads(said.stdout) if said.returncode == 0 else None
    except ValueError:
        found = None
    if not isinstance(found, dict) or not isinstance(found.get("set"), bool):
        return [f"Could not read whether Paseo's daemon has a password (`paseo daemon config get daemon.auth.password` as {USER}): "
                "run `user` again"]
    if found["set"]:
        return []
    print(f"Paseo's daemon of {USER} needs a password of its own: type a new one twice (keep it in your password manager; "
          "the orchestrator's user gets the same one, as `admin` printed).")
    sh.tty_must([cli, "daemon", "set-password"], cwd=home)
    return ["Paseo's daemon takes the new password at its next start: `paseo daemon stop` as the agent when no agent runs"]


def keys_env(sh: Sh, home: Path, environ: dict[str, str], keys: Sequence[tuple[str, str]],
             optional: Sequence[tuple[str, str]] = (), profile: str = DEFAULT_PROFILE, review_profile: str = "") -> dict[str, str]:
    """The environment with these Keychain keys and no other: every key variable of the caller's environment goes first
    (`CALLER_KEYS`), and the shim folders come first on PATH (`shim_folders`), so `gh` is the shim with the app's token
    (mise's own gh, which `mise activate` puts first in an interactive shell, has no token). A key of `optional`
    that the Keychain does not hold gives no variable, and no refusal. Every caller also gets LOCAL_REVIEW_COMMAND, on the
    inference profile `profile`, or `review_profile` when that is given: a cell of a mixed start runs on its own profile and
    reviews with the profile in force's model. Its model and effort level are read first, so a settings file that
    names no review for a profile stops the start before this reads a key."""
    reviewing = review_profile or profile
    review = review_settings()[reviewing]
    env = {k: v for k, v in environ.items() if k not in CALLER_KEYS}
    for name, var in keys:
        value = keychain_get(sh, name)
        if not value:
            raise Refused(f"the {name} is not in the Keychain: run `user`")
        env[var] = value
    for name, var in optional:
        value = keychain_get(sh, name)
        if value:
            env[var] = value
    env["LOCAL_REVIEW_COMMAND"] = review_command(PROFILES[reviewing], home / "Library" / "Keychains" / KEYCHAIN, review, reviewing)
    env["PATH"] = os.pathsep.join([*map(str, shim_folders(home)), environ.get("PATH", "/usr/bin:/bin")])
    return env


def session_env(sh: Sh, home: Path, environ: dict[str, str], profile: str = DEFAULT_PROFILE,
                review_profile: str = "") -> dict[str, str]:
    """The environment of the profile's harness: the inference profile's key, when it has one (a profile on a provider that
    its harness signs in to itself has none), and its provider's variables, in that harness's terms
    (`claude`, the subscription's token and no API key, since Claude Code prefers ANTHROPIC_API_KEY to
    the token without a word; `pi-ollama`, Ollama Cloud's key, and no Claude token), the Linear key, and the measuring key when the Keychain holds one: under its own name, so no
    command of the product and no test of the run calls a model by accident (the product reads OPENROUTER_API_KEY)."""
    chosen = PROFILES[profile]
    own = ((chosen.key, chosen.variable),) if chosen.key else ()  # a sign-in profile's harness holds its own
    env = keys_env(sh, home, environ, (*own, *SESSION_KEYS), SESSION_OPTIONAL_KEYS, profile, review_profile)
    env.update(provider_env(chosen))
    return env


def own_variables(sh: Sh, name: str) -> dict[str, str]:
    """The variables that the profile `name` alone sets: its provider's variables and its key's (a profile on its harness's own
    sign-in has no key)."""
    chosen = PROFILES[name]
    found = dict(provider_env(chosen))
    if chosen.key:
        value = keychain_get(sh, chosen.key)
        if not value:
            raise Refused(f"the {chosen.key} is not in the Keychain: run `user`")
        found[chosen.variable] = value
    return found


def daemon_env(sh: Sh, home: Path, environ: dict[str, str], profile: str) -> dict[str, str]:
    """The environment of Paseo's daemon: the session of `profile`, and the key and the provider variables of
    each other profile that its cells run on (`profile_set`). Each agent inherits all of it, and the orchestrator's start
    command empties the keys that an agent must not hold (`paseo run --env VAR=`). The local review stays the one of
    `profile`. Two profiles that set one variable to two values stop the start: an agent could not hold both. So does a
    provider variable that one profile sets and another profile of the same harness leaves unset: the agent of
    the second would send its key to the first one's provider."""
    env = session_env(sh, home, environ, profile)
    setter = {var: profile for var in (*dict(provider_env(PROFILES[profile])), PROFILES[profile].variable) if var}
    for other in profile_set(profile)[1:]:
        for var, value in own_variables(sh, other).items():
            if var in env and env[var] != value:
                raise Refused(f"the profiles {setter.get(var, 'of the session')} and {other} set {var} to two values: the "
                              "daemon's agents inherit one environment, so a mix cannot hold both")
            env[var] = value
            setter.setdefault(var, other)
    names = profile_set(profile)
    for one in names:
        for var, _ in provider_env(PROFILES[one]):
            for two in names:
                if PROFILES[two].harness == PROFILES[one].harness and var not in dict(provider_env(PROFILES[two])):
                    raise Refused(f"the profile {one} sets {var} and {two} does not: the daemon's agents inherit one "
                                  f"environment, so the agent of {two} would send its key to the provider of {one}")
    return env


def session_command(home: Path, profile: str, args: Sequence[str]) -> tuple[str, list[str]]:
    """The program that `session --profile NAME` starts and its argv: the profile's harness's CLI in ~/.local/bin, with the
    arguments that follow the profile."""
    binary = HARNESSES[PROFILES[profile].harness].binary
    return str(home / ".local" / "bin" / binary), [binary, *args]


def take_profile(args: Sequence[str]) -> tuple[str, str, list[str]]:
    """The inference profile of `session --profile NAME [--review-profile NAME] ...` (the orchestrator's `{profile}`),
    the profile whose local review the session gets, and the harness's own arguments. DEFAULT_PROFILE without the option (an
    `agent` or `claude` typed by hand). The review profile is the profile in force of a mixed start; it is the
    session's own profile without the option."""
    if args[:1] != ["--profile"]:
        return DEFAULT_PROFILE, DEFAULT_PROFILE, list(args)
    if len(args) < 2 or args[1] not in PROFILES:
        raise Refused(f"--profile takes one of {', '.join(PROFILES)}")
    profile, rest = args[1], list(args[2:])
    if rest[:1] != ["--review-profile"]:
        return profile, profile, rest
    if len(rest) < 2 or rest[1] not in PROFILES:
        raise Refused(f"--review-profile takes one of {', '.join(PROFILES)}")
    return profile, rest[1], rest[2:]


WORKERS_VARIABLE = "PYTEST_XDIST_AUTO_NUM_WORKERS"  # the number of workers that `pytest -n auto` starts


def take_workers(args: Sequence[str]) -> tuple[int, list[str]]:
    """`session ... [--workers N] ...` (the orchestrator's `{workers_flag}`), which comes right after the profile options: the run's share of
    pytest workers (0 without the option) and the harness's own arguments. A value that is no whole number from 1 to 999 is refused."""
    if args[:1] != ["--workers"]:
        return 0, list(args)
    if len(args) < 2 or not re.fullmatch(r"[1-9][0-9]{0,2}", args[1]):
        raise Refused("--workers takes a whole number from 1 to 999")
    return int(args[1]), list(args[2:])


def run_env(sh: Sh, home: Path, environ: dict[str, str]) -> dict[str, str]:
    """`run`'s environment: the Linear key only. No Claude token: the orchestrator starts each session in a
    herdr pane, whose `claude` alias reads the token itself, so the process that only polls holds the least key."""
    return keys_env(sh, home, environ, (("linear-key", "LINEAR_API_KEY"),))


# Paseo's daemon starts each agent itself, so the agents get their keys from the daemon's environment. This is
# the only command that gets the session's keys. The status commands do not get the keys.
PASEO_PROFILE = ".local/state/orchestrator/paseo-profile.json"


def check_provider(home: Path, name: str) -> bool:
    """Compare a profile's custom provider with the catalog, in pi's models.json.
    A built-in provider is the harness's own."""
    profile = PROFILES[name]
    path = HARNESSES[profile.harness].provider_file
    if not path or profile.provider:
        return True
    try:
        data = json.loads((home / path).read_text())
    except (OSError, ValueError):
        return False
    under, entry = PROVIDER_ENTRIES[profile.harness]
    providers = data.get(under) if isinstance(data, dict) else None
    return isinstance(providers, dict) and providers.get(name) == entry(name, profile)


def paseo_status(sh: Sh, cli: str) -> dict[str, object]:
    try:
        done = sh.out([cli, "daemon", "status", "--json"])
    except OSError:
        raise Refused("cannot run Paseo: run `agent_user.py user` to set up its CLI") from None
    if done.returncode == 0:
        try:
            status = json.loads(done.stdout)
        except ValueError:
            pass
        else:
            if isinstance(status, dict):
                return status
    raise Refused("cannot read the Paseo daemon's status: run `paseo daemon status --json`")


def start_paseo(sh: Sh, home: Path, profiles: Sequence[str], env: dict[str, str], review: str = "", listen: str = "") -> int:
    """Record the profiles (the one whose review the daemon holds first, then the others whose keys it holds) only
    for the daemon that this command starts, not one already running. With `listen` (ADDRESS:PORT, a project's own
    port), Paseo's config gets it as `daemon.listen` first, which the daemon reads at its start (measured 2026-11-26,
    Paseo 0.17.4: `paseo daemon config set` writes ~/.paseo/config.json and asks nothing)."""
    cli = str(home / ".local/bin/paseo")
    marker = home / PASEO_PROFILE
    marker.parent.mkdir(parents=True, exist_ok=True)
    with marker.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if paseo_status(sh, cli).get("localDaemon") != "stopped":
            raise Refused("Paseo's daemon is not stopped: stop it when no agent runs, then run `paseo-daemon` again")
        if listen:
            sh.must([cli, "daemon", "config", "set", "daemon.listen", listen, "--string"])
        code = sh.tty([cli, "daemon", "start"], cwd=home, env=env)
        if code:
            return code
        status = paseo_status(sh, cli)
        pid, started = status.get("pid"), status.get("startedAt")
        if status.get("localDaemon") != "running" or type(pid) is not int or not isinstance(started, str) or not started:
            raise Refused("Paseo did not report a running daemon with a start time")
        replace_json(marker, {"pid": pid, "startedAt": started, "profile": profiles[0], "profiles": list(profiles),
                             "review": review})
    return 0

# Paseo's daemon listens on this Mac's Tailscale address, which comes only once Tailscale is connected: right after a restart a
# start fails with EADDRNOTAVAIL and a stack trace (2026-11-19). So `paseo-daemon` waits for the address first.
TAILNET_WAIT_TRIES = 30
TAILNET_WAIT_SECONDS = 2.0
NO_TAILNET = ("Tailscale is not connected: this Mac has no address in 100.64.0.0/10, and Paseo's daemon listens on it. "
              "Connect Tailscale, then run this command again")


def tailnet_address(sh: Sh) -> str | None:
    """This Mac's first IPv4 address in Tailscale's range, from `ifconfig`, or None."""
    net = ipaddress.ip_network(TAILNET[0])
    for found in re.finditer(r"^\s*inet (\d+\.\d+\.\d+\.\d+)\b", sh.out(["ifconfig"]).stdout, re.MULTILINE):
        if ipaddress.ip_address(found.group(1)) in net:
            return found.group(1)
    return None


def wait_for_tailnet(sh: Sh, sleep: Callable[[float], None] = time.sleep) -> str | None:
    """The Tailscale address, after a wait of up to TAILNET_WAIT_TRIES x TAILNET_WAIT_SECONDS, or None when it never came."""
    for attempt in range(TAILNET_WAIT_TRIES):
        address = tailnet_address(sh)
        if address:
            return address
        if attempt == 0:
            print(f"Waiting for Tailscale (up to {int(TAILNET_WAIT_TRIES * TAILNET_WAIT_SECONDS)} s): Paseo's daemon listens on its address")
        if attempt < TAILNET_WAIT_TRIES - 1:
            sleep(TAILNET_WAIT_SECONDS)
    return None

# The orchestrator's service (the owner's call of 2026-11-18): a LaunchAgent that orchestrator/deploy/install.py
# writes, root's, and loads into this user's domain `user/UID`. launchd loads it there itself at the first SSH login after a
# restart, and the domain keeps that login's audit session. A pane has a session of its own, so the unlock typed here
# does not reach the service: the owner runs `owlcode unlock`. `paseo-daemon` loads the job only
# when it is not loaded (after a bootout).
# The label is the config's `[service] label`: the example's is neutral, so the installed config must carry this one (a test
# checks that a config with it installs the plist that is loaded here).
ORCHESTRATOR_LABEL = f"com.{USER}.orchestrator"
ORCHESTRATOR_PLIST = Path("/Library/LaunchAgents") / f"{ORCHESTRATOR_LABEL}.plist"
UNLOCK_NOTE = ("; the unlock here does not reach it: from your account, run `owlcode unlock` once after "
               "each restart")


def load_orchestrator(sh: Sh, uid: int) -> str:
    """Load the orchestrator's service into this user's domain when it is installed and not loaded. Returns one line for
    the pane. A failure is a line too, never a stop: Paseo's daemon starts all the same."""
    if not ORCHESTRATOR_PLIST.exists():
        return f"the orchestrator's service: not installed ({ORCHESTRATOR_PLIST} is missing)"
    domain = f"user/{uid}"
    if sh.out(["launchctl", "print", f"{domain}/{ORCHESTRATOR_LABEL}"]).returncode == 0:
        return f"the orchestrator's service: loaded already in {domain}{UNLOCK_NOTE}"
    done = sh.out(["launchctl", "bootstrap", domain, str(ORCHESTRATOR_PLIST)])
    if done.returncode:
        return (f"the orchestrator's service: not loaded ({(done.stderr or done.stdout).strip()[:200]}); from your "
                f"account: sudo launchctl bootstrap {domain} {ORCHESTRATOR_PLIST}")
    return f"the orchestrator's service: loaded in {domain}{UNLOCK_NOTE}"


# ---- check: run as root from the owner's checkout ----

class Line(NamedTuple):
    status: str
    name: str
    detail: str = ""


def pi_sign_in(home: Path, provider: str) -> list[Line]:
    """Whether pi is signed in to `provider`, for a profile that runs on the harness's own sign-in: its auth.json
    holds that provider's entry, and only its own user reads the file. The tokens stay in the file, and this never prints,
    copies or returns one, so `key_check` shows that the sign-in is there without a key leaving it."""
    path = home / PI_AUTH_FILE
    where = f"{path} (pi's /login as {USER})"
    unreadable = [Line(FAIL, f"pi sign-in: {path} is not readable here (run this as {USER})")]
    try:  # another account's home: pathlib does not ignore EACCES, so `is_file` raises instead of answering no
        there = path.is_file()
    except OSError:
        return unreadable
    if not there:
        return [Line(FAIL, f"pi sign-in: no {provider} sign-in in {where}")]
    if not os.access(path, os.R_OK):  # a file that the check can see and not read: the same answer
        return unreadable
    open_to_others = "" if closed(path) else f"chmod 600 {path}"  # pi writes 600: another mode is not pi's own file
    data: object = None
    for attempt in range(2):  # pi rewrites the file under its own lock: a read at that moment catches half of it
        try:
            data = json.loads(path.read_text())
            break
        except (OSError, ValueError) as e:
            error = type(e).__name__ + (" (try again)" if attempt == 0 else "")
            time.sleep(0.25)
    else:
        return [Line(FAIL, f"pi sign-in: no {provider} sign-in in {where}", "; ".join(filter(None, (error, open_to_others))))]
    if not isinstance(data, dict) or not isinstance(data.get(provider), dict):
        return [Line(FAIL, f"pi sign-in: no {provider} sign-in in {where}", open_to_others)]
    if open_to_others:
        return [Line(FAIL, f"pi sign-in: {provider} is signed in, and its file is open to others", open_to_others)]
    return [Line(OK, f"pi sign-in: {provider} is signed in")]


def check(sh: Sh, owner: str, owner_home: Path, home: Path, phones: Path = PHONES) -> list[Line]:
    lines: list[Line] = []

    def line(good: bool, name: str, detail: str) -> None:
        lines.append(Line(OK if good else FAIL, name, detail))

    if sh.out(["dscl", ".", "-read", f"/Users/{USER}"]).returncode:
        return [Line(FAIL, "user", f"no user {USER}: run `admin`")]
    line(not member(sh, USER, "admin"), "not an admin", f"{USER} must be a Standard user")
    line(not member(sh, USER, "staff"), "not in staff", "staff can read the people's homes (750)")
    group = sh.out(["id", "-gn", USER]).stdout.strip()
    line(group == GROUP, "primary group", f"{group or 'unknown'}, expected {GROUP}")
    line(closed(owner_home), "owner's home 700", f"{owner_home}: run `chmod 700 ~` from your account")
    line(home.exists() and closed(home), "agent's home 700", f"{home}: run `admin` again")
    line(sh.out(["sudo", "-u", USER, "ls", str(owner_home)]).returncode != 0, "owner's home closed to the agent",
         f"{USER} can list {owner_home}")
    ssh = home / ".ssh"
    files = [p for p in ssh.glob("*") if p.is_file() and not p.is_symlink()] if ssh.is_dir() and not ssh.is_symlink() else []  # root follows none
    keys = [p.name for p in files if b"PRIVATE KEY" in p.read_bytes()]
    line(not keys, "no SSH key", f"private keys in {ssh}: {', '.join(keys)} (an agent must not reach the server)")
    bw = home / "Library" / "Application Support" / "Bitwarden CLI"
    line(not bw.exists(), "no Bitwarden", f"{bw} exists: log out of bw as {USER} and delete it")
    gh = ["sudo", "-u", USER, "-H", "/usr/bin/python3", "-I", str(home / SCRIPT), "gh", "api"]
    here = home if home.is_dir() else Path("/")  # a folder the agent reads; `admin` that stopped early left no home
    repos = sh.out([*gh, "/installation/repositories", "--jq", "[.repositories[].full_name]"], cwd=here)
    if repos.returncode:
        said = (repos.stderr or repos.stdout).strip().splitlines()[-1:]  # an error line names no token
        line(False, "app token", f"gh could not get the app's token: {said[0][:200] if said else 'no message'}")
    else:
        seen = sorted(json.loads(repos.stdout or "[]"))
        # the project's file and the clone's AGENTS.md are the agent's files: read them as the agent, so root never reads
        # through the agent's links
        project = agent_project(sh, home, here)
        table = sh.out(["sudo", "-u", USER, "-H", "/bin/cat", "--", str(home / CLONE / "AGENTS.md")], cwd=here)
        want = sorted(loop_repos(table.stdout if table.returncode == 0 else "", project))
        extra, missing = sorted(set(seen) - set(want)), sorted(set(want) - set(seen))
        problems = [f"{text}: {', '.join(names)}" for text, names in (("not in the loop", extra), ("missing", missing)) if names]
        line(not problems, "the app sees the loop's repositories only", "; ".join(problems) + " (in the app's settings "
             + (f"(Install App, Configure) choose only {project.repo}, the repository of team {project.team})" if project else
                f"(Install App, Configure) choose exactly the repositories of \"The teams\" in {home / CLONE / 'AGENTS.md'})"))
        line(sh.out([*gh, "/user"], cwd=here).returncode != 0, "gh is the app, not a person", "gh acts as a user account")
    ssh_lines(sh, owner, owner_home, home, line, phones)
    paseo_lines(sh, home, here, line)
    return lines


def agent_project(sh: Sh, home: Path, here: Path) -> Project | None:
    """The project file of the agent's home, read as the agent (root follows none of its links); None when there is none
    or it does not read as a project."""
    read = sh.out(["sudo", "-u", USER, "-H", "/bin/cat", "--", str(home / PROJECT_FILE)], cwd=here)
    try:
        return project_from(json.loads(read.stdout)) if read.returncode == 0 else None
    except ValueError:
        return None


def version(text: str) -> str:
    found = VERSION.search(text)
    return found.group(0) if found else "unknown"


def installed_version(sh: Sh, binary: Path) -> str:
    """The version a CLI reports, or `unknown`: `user` installs the pinned one again when they differ, since a
    CLI that does not update itself (pi, with `PI_OFFLINE=1`) never reaches a new pin by itself."""
    return version(sh.out([str(binary), "--version"]).stdout)


def paseo_lines(sh: Sh, home: Path, here: Path, line: Callable[[bool, str, str], None], app: Path | None = None) -> None:
    """Paseo: the agent's paseo is the app's CLI, and the app, that CLI and the agent's daemon have one version.
    Each command runs as the agent. A daemon that does not run is a line of its own, never a FAIL. The check never
    restarts the daemon: a restart kills the agents that run."""
    app = app or PASEO_APP
    if not app.is_file():
        line(True, f"paseo: no Paseo app at {app}, nothing to compare", "")
        return
    link = home / ".local" / "bin" / "paseo"
    linked = link.is_symlink() and os.readlink(link) == str(app)  # readlink follows nothing (root follows no symlink)
    line(linked, "paseo is the app's CLI", f"{link} is not a link to {app}: run `user` again")
    as_agent = ["sudo", "-u", USER, "-H"]
    app_v = version(sh.out([*as_agent, str(app), "--version"], cwd=here).stdout)
    cli_v = version(sh.out([*as_agent, str(link), "--version"], cwd=here).stdout) if os.path.lexists(link) else "missing"
    status = sh.out([*as_agent, str(link), "daemon", "status", "--json"], cwd=here) if cli_v != "missing" else None
    try:
        daemon = json.loads(status.stdout) if status and status.returncode == 0 else {}
    except ValueError:
        daemon = {}
    running = isinstance(daemon, dict) and daemon.get("localDaemon") == "running"
    daemon_v = version(str(daemon.get("daemonVersion") or "")) if running else "not running"
    name = f"paseo: app {app_v}, CLI {cli_v}, daemon {daemon_v}"
    restart = (f"restart the agent's daemon when `paseo ls -g --json` shows no agent running: as {USER}, `paseo daemon "
               f"stop`, then `/usr/bin/python3 -I ~/{SCRIPT} paseo-daemon`")
    if "unknown" in (app_v, cli_v):
        line(False, name, "could not read paseo's version: run `paseo --version` as the agent and read its output")
    elif cli_v != app_v:
        line(False, name, "the CLI is not the app's: run `user` again")
    elif running and daemon_v == "unknown":
        line(False, name, "could not read the daemon's version: run `paseo daemon status --json` as the agent and read its output")
    elif running and daemon_v != app_v:
        line(False, name, f"the daemon runs another version: {restart}")
    else:
        line(True, name, "")


SSHD_WANTS = {"passwordauthentication": "no", "kbdinteractiveauthentication": "no", "allowtcpforwarding": "local",
              "allowstreamlocalforwarding": "local", "permitlisten": "none", "allowagentforwarding": "no",
              "x11forwarding": "no", "permittunnel": "no"}


def ssh_lines(sh: Sh, owner: str, owner_home: Path, home: Path, line: Callable[[bool, str, str], None], phones: Path) -> None:
    """The guards of the herdr machine: sshd's rules for the agent, its key's options, the owner's host entry,
    and herdr as the agent's own file."""
    said = sh.out(["sshd", "-T", "-C", f"user={USER},host=localhost,addr=127.0.0.1"]).stdout
    got: dict[str, str] = {}
    allowed: list[str] = []
    for printed in said.lower().splitlines():
        name, _, value = printed.partition(" ")
        if name == "allowusers":  # sshd -T prints a list one value a line
            allowed += value.split()
        elif value:
            got[name] = value
    wrong = [f"{k} {got.get(k, '?')}" for k, v in SSHD_WANTS.items() if got.get(k) != v]
    # this Mac and the tailnet, never the home LAN. Each project's own user has its own conf file (`000-NAME.conf`), and
    # sshd adds up their AllowUsers lines: the entries of another user give this one nothing. sshd reads an entry's user
    # part as a pattern, so an entry whose pattern matches this user (`*@HOST`) stays and is compared
    allowed = [a for a in allowed if fnmatch.fnmatchcase(USER.lower(), a.split("@", 1)[0])]
    if sorted(allowed) != sorted(a.lower() for a in ALLOWED):
        wrong.append(f"allowusers {' '.join(allowed) or '?'}")
    line(not wrong, "sshd: the agent only, with a key, no listening or agent forwarding", f"{', '.join(wrong)}: run `admin` again")
    keys = home / ".ssh" / "authorized_keys"
    lines_ = [l for l in keys.read_text().splitlines() if l.strip() and not l.startswith("#")] \
        if keys.is_file() and not keys.is_symlink() and not keys.parent.is_symlink() else []  # root follows no symlink
    yes = approved(phones) if lines_[1:] else set()
    odd = [f"{fingerprint(key_part(l) or l)} ({' '.join((key_part(l) or l).split()[2:]) or 'no comment'})" for l in lines_[1:]
           if not l.startswith(PHONE_OPTIONS + " ") or one_time(l) or key_id(key_part(l)) not in yes]
    line(bool(lines_) and lines_[0].startswith(KEY_OPTIONS + " ") and not odd, "the agent's keys: herdr's, and the phones you approved",
         f"{keys}: herdr's key first with {KEY_OPTIONS}, then only keys you approved with {PHONE_OPTIONS}"
         f"{'; not so: ' + ', '.join(odd) if odd else ''}: run `admin` again")
    entry = sh.out(["sudo", "-u", owner, "-H", "ssh", "-G", USER]).stdout.lower().splitlines()
    line({"hostname 127.0.0.1", f"user {USER}", "forwardagent no"} <= set(entry), "your ssh config: no agent forwarding",
         f"the Host {USER} block that `admin` prints goes at the top of {owner_home / '.ssh' / 'config'}")
    herdr = home / ".local" / "bin" / "herdr"
    links = [p for p in (herdr.parent.parent, herdr.parent, herdr) if p.is_symlink()]  # root follows no symlink
    info = None if links or not herdr.is_file() else herdr.stat()
    line(info is not None and info.st_uid == home.stat().st_uid and not info.st_mode & 0o022, "herdr is the agent's own file",
         f"{herdr} is missing, a link, or a file another user can write: `herdr machine add {USER}` from your account "
         "installs it" + (f"; {links[0]} is a link" if links else ""))
    shim = home / ".local" / "share" / "mise" / "shims" / "herdr"
    line(not os.path.lexists(shim), "no herdr in the agent's mise", f"{shim} is there: run `user` again")


# ---- the command line ----

def model_table() -> str:
    def rates(price: dict[str, float]) -> str:
        return "/".join(f"${price[key]:g}" for key in ("input", "cached_input", "output"))

    rows = [("Provider", "Model ID", "Harnesses", "Effort levels", "Tier", "USD/M input/cached/output")]
    for provider_id, provider in CATALOG["providers"].items():
        for model_id, model in provider["models"].items():
            price = model.get("prices")
            cost = rates(price) if price else "subscription"
            if price and "off_peak" in price:
                cost += f"; off-peak {rates(price['off_peak'])}"
            if price and "promo_output" in price:
                cost += f"; promo output ${price['promo_output']:g}"
            supported = []
            for harness, group in model["efforts"].items():
                levels = tuple(effort if effort == sent else f"{effort}→{sent}"
                               for effort, sent in CATALOG["efforts"][group].items() if sent is not None)
                supported.append((harness, levels))
            if len({frozenset(levels) for _, levels in supported}) == 1:
                supported = [(", ".join(harness for harness, _ in supported), supported[0][1])]
            for harnesses, levels in supported:
                rows.append((provider_id, model_id, harnesses, ", ".join(levels), model.get("tier", "-"), cost))
    widths = [max(map(len, column)) for column in zip(*rows)]
    return "\n".join("  ".join(value.ljust(width) for value, width in zip(row, widths)).rstrip() for row in rows)


def check_model_lists(sh: Sh) -> int:
    failed = False
    for name, provider in CATALOG["providers"].items():
        endpoint = provider.get("model_list")
        if not endpoint:
            continue
        key = keychain_get(sh, provider["keychain"])
        if not key:
            print(f"{name}: no Keychain key; run `user` to add it")
            failed = True
            continue
        if "\r" in key or "\n" in key:
            print(f"{name}: invalid Keychain key")
            failed = True
            continue
        try:
            reply = sh.out(["/usr/bin/curl", "--disable", "--silent", "--fail", "--max-time", "20", "-H", "@-", endpoint["url"]],
                           stdin=f"Authorization: Bearer {key}\n")
            if reply.returncode:
                raise ValueError
            items = json.loads(reply.stdout)[endpoint["items"]]
            if not isinstance(items, list):
                raise ValueError
            listed = {item[endpoint["id"]] for item in items}
            if not all(isinstance(model, str) and model.isprintable() for model in listed):
                raise ValueError
        except (IndexError, KeyError, OSError, TypeError, ValueError):
            print(f"{name}: could not read its model list")
            failed = True
            continue
        known = set(provider["models"])
        missing = sorted(known - listed)
        print(f"{name}: catalog IDs not listed: {', '.join(missing) or '(none)'}")
        print(f"{name}: listed IDs not in catalog: {', '.join(sorted(listed - known)) or '(none)'}")
        failed |= bool(missing)
    return int(failed)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    admin_parser = sub.add_parser("admin", help="make the agents' user; with --team, the user of one project (`owlcode admin --project TEAM`)")
    admin_parser.add_argument("--team", help="the team's key: the user is that project's own")
    admin_parser.add_argument("--repo", help="the team's repository, OWNER/NAME: the user's one clone")
    admin_parser.add_argument("--paseo-port", type=int, default=0, help="the port of the user's Paseo daemon")
    admin_parser.add_argument("--orchestrator-user", default="", help="the orchestrator's user, whose keychain holds the daemon's password")
    sub.add_parser("user").add_argument("--replace", action="store_true", help="ask for every key again")
    sub.add_parser("check")
    sub.add_parser("models", help="show the inference catalog's combinations and prices")
    sub.add_parser("models-check", help="compare the catalog with the providers' model lists")
    sub.add_parser("pi-signin", help="whether pi is signed in to a provider that signs itself in").add_argument(
        "--provider", required=True, help="pi's provider id, such as openai-codex (the ChatGPT subscription)")
    sub.add_parser("provider-check", help="check the profile's custom provider (pi) against the catalog").add_argument(
        "--profile", required=True, choices=sorted(name for name, profile in PROFILES.items() if profile.harness in PROVIDER_ENTRIES))
    sub.add_parser("session").add_argument("args", nargs=argparse.REMAINDER)
    sub.add_parser("pi", help="session, for a pi profile only (the pane's `pi` alias)").add_argument(
        "args", nargs=argparse.REMAINDER)
    sub.add_parser("git-credential").add_argument("action")
    sub.add_parser("gh").add_argument("args", nargs=argparse.REMAINDER)
    sub.add_parser("app-check", help="whether the orchestrator's app reaches each product's repository (owlcode check)")
    sub.add_parser("run", help="run -- CMD [ARGS...]").add_argument("args", nargs=argparse.REMAINDER)
    sub.add_parser("paseo-daemon", help="start Paseo's daemon with a session's keys").add_argument(
        "--profile", choices=sorted(PROFILES), help="the inference profile of its agents: only the one in force")
    if argv[:1] in (["gh"], ["session"], ["pi"], ["run"]):  # gh's, the harness's and the command's own flags are not this script's
        args = argparse.Namespace(command=argv[0], args=argv[1:])
    else:
        args = parser.parse_args(argv)
    sh = Sh()
    me = pwd.getpwuid(os.getuid())
    home = Path(f"/Users/{USER}")
    try:
        if args.command == "models":
            print(model_table())
            return 0
        if args.command == "models-check":
            if me.pw_name != USER:
                raise Refused(f"run models-check as {USER}, which holds the provider keys")
            unlock(sh)
            return check_model_lists(sh)
        if args.command in ("admin", "check"):
            owner = os.environ.get("SUDO_USER", "")
            if os.geteuid() != 0 or not owner or owner in (USER, "root"):
                raise Refused("run it with sudo from your own account: "
                              f"sudo /usr/bin/python3 -I .workflow/approved_run.py .workflow/{SCRIPT} -- {args.command}")
            owner_home = Path(pwd.getpwnam(owner).pw_dir)
            if args.command == "admin":
                project = None
                if args.team or args.repo or args.paseo_port:
                    try:
                        project = project_from({"team": args.team, "repo": args.repo, "paseo_port": args.paseo_port}, "admin")
                    except ValueError as e:
                        raise Refused(str(e)) from None
                    if not NAME_RULE.fullmatch(args.orchestrator_user):
                        raise Refused("admin --team needs --orchestrator-user NAME: `owlcode admin --project TEAM` gives it")
                    if args.orchestrator_user == USER:  # its project file would limit the service's tokens to one repository
                        raise Refused(f"{USER} is the orchestrator's user: a project's user is another user (the agent_user "
                                      "of the team's table)")
                for note in admin(sh, owner, owner_home, home, Path(__file__).read_text(), ask=Ask(), project=project,
                                  orchestrator_user=args.orchestrator_user):
                    print(note)
                return 0
            lines = check(sh, owner, owner_home, home)
            for status, name, detail in lines:
                print(f"{status:4} {name}" + (f": {detail}" if status != OK else ""))
            return 1 if any(status == FAIL for status, _, _ in lines) else 0
        if args.command == "provider-check":
            return 0 if check_provider(Path(me.pw_dir), args.profile) else 1
        if args.command == "pi-signin":
            lines = pi_sign_in(home, args.provider)
            for status, name, detail in lines:
                print(f"{status:4} {name}" + (f": {detail}" if detail else ""))
            return 1 if any(status == FAIL for status, _, _ in lines) else 0
        if args.command == "user":
            for note in user(sh, Ask(), Path(me.pw_dir), me.pw_name, args.replace):
                print(note)
            return 0
        if args.command in ("session", "pi"):
            profile, review_profile, rest = take_profile(args.args)
            workers, rest = take_workers(rest)
            if args.command == "pi" and (args.args[:1] != ["--profile"] or PROFILES[profile].harness != "pi"):
                raise Refused("pi takes --profile NAME of a pi profile: " + ", ".join(
                    name for name, p in PROFILES.items() if p.harness == "pi"))
            unlock(sh)
            env = session_env(sh, Path(me.pw_dir), dict(os.environ), profile, review_profile)
            if workers:
                env[WORKERS_VARIABLE] = str(workers)
            if rest[:1] == ["--keep-cwd"]:  # A detached run starts in the product's own run_cwd.
                rest = rest[1:]
            else:
                os.chdir(Path(me.pw_dir) / CLONE)
            program, argv_ = session_command(Path(me.pw_dir), profile, rest)
            os.execve(program, argv_, env)  # `claude --resume` too
        if args.command == "run":
            cmd = args.args[1:] if args.args[:1] == ["--"] else args.args
            if not cmd:
                raise Refused(f"name the command: /usr/bin/python3 -I {SCRIPT} run -- CMD [ARGS...]")
            unlock(sh)
            env = run_env(sh, Path(me.pw_dir), dict(os.environ))
            os.execvpe(cmd[0], cmd, env)  # found on the new PATH, so `gh` is the shim; the folder stays the caller's
        if args.command == "paseo-daemon":
            if me.pw_name != USER:  # the first try ran it in the owner's account
                raise Refused(f"run it as {USER}, in a pane on the machine {USER}: /usr/bin/python3 -I ~/{SCRIPT} paseo-daemon")
            profile = profile_in_force()
            if args.profile not in (None, profile):  # the config is the one place of the profile
                raise Refused(f"--profile {args.profile} is not the orchestrator's inference_profile, {profile}: from your account, "
                              f"run `owlcode set inference_profile={args.profile}`; the service then restarts "
                              "this daemon when no agent runs")
            unlock(sh)
            print(load_orchestrator(sh, os.getuid()))  # loads the job only when it is not loaded; the pane's unlock does not reach it
            address = wait_for_tailnet(sh)
            if address is None:
                raise Refused(NO_TAILNET)
            profiles = profile_set(profile)
            env = daemon_env(sh, Path(me.pw_dir), dict(os.environ), profile)
            print(f"Paseo's daemon: inference profile {profile}, with the keys of {', '.join(profiles)} (a switch: "
                  "`owlcode set inference_profile=NAME` from your account; the service restarts this daemon when no agent "
                  "runs, and its [profiles.NAME] cells name the models of the runs and the profiles that run them)")
            listen = f"{address}:{PROJECT.paseo_port}" if PROJECT and PROJECT.paseo_port else ""
            return start_paseo(sh, Path(me.pw_dir), profiles, env, review_digest(profiles), listen)
        if args.command == "git-credential":
            sys.stdout.write(git_credential(sh, args.action, sys.stdin.read()))
            return 0
        if args.command == "app-check":
            app = service_app()
            if app is None:
                print(f"ok   github: no orchestrator's app is set ([service] app_client_id): the service uses {USER}'s implementer key")
                return 0
            reach = app_check(sh, app)
            for status, text in reach:
                print(f"{status:4} {text}")
            return 1 if any(status == FAIL for status, _ in reach) else 0
        gh = real_gh(sh)
        app = service_app()
        token = service_app_token(sh, app, call_repo(args.args, os.environ, app.repos[0])) if app else app_token(sh)
        env = dict(os.environ, GH_TOKEN=token)
        os.execve(gh, [gh, *args.args], env)
    except Refused as e:
        print(f"agent_user.py: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
