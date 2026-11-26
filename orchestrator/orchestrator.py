#!/usr/bin/env python3
"""The orchestrator: it polls Linear and GitHub, decides what starts, starts one session for a fix that no
session took, and escalates it when that one takes it neither.

Standard library only, Python 3.11 or newer, no macOS-only call but the keychain read of a session endpoint's password
(`keychain_password`). It names no product: the Linear team, the repository and the
state and label names come from a config file (`orchestrator.example.toml` holds this product's values). It trusts artifacts
only (agents/README.md "What the orchestrator trusts"): the open issues, the open pull requests, their checks and reviews. It
keeps no state of its own, so a crash costs nothing: the next round reads the same artifacts. The one exception is the record
of each session's host in the state file (`Hosts`): a lost record costs only the wakes and the closes of an old host.

The decisions are pure functions (`can_start`, `can_resume`, `pr_state`, `pick_starts`); the reads and the writes are small functions
that a test replaces. A run is a session in a session host (`start_run`): herdr, Paseo, or a plain process. Each developer picks
the host in their own config (the owner's call, 2026-11-11): the host is a personal view of the runs. A switch of `session_host` needs no wait (`Hosts`): new sessions start on the new host, and a session of the old host gets its wakes and its close there. With `dry_run` it prints
what it would do and writes nothing to Linear or GitHub. The session that opened a pull request runs its own fix round (the owner's call,
2026-11-10). When an issue waits for a fix, the orchestrator first wakes that session with a message, once for each event, when the host shows it
idle (`wake_events`). When no session took the fix within `claim_wait_seconds`, it starts one new
session for the fix (the owner's call, 2026-11-16): within `max_runs`, with the run line `Run: MODEL, effort EFFORT (fix
round of PR #N)`, and never beside a busy session of the key (or one that this process started and the host does not list). One start for each wait: when that session also
takes no fix within a second `claim_wait_seconds`, or the pull request waits for a fix again after it, the orchestrator moves the
issue to Escalated with one comment. It starts the issues in Todo that can
start, within `max_runs`: none related to an issue that runs, and one per Area while `distinct_areas` is on. A
follow-up (`From: PR #N`) starts only when pull request N is no longer open (`unmerged_origin`; each of its
`From: PR #N` lines for a follow-up that collects several reviews' findings).
Each run gets the model and the effort level that the issue's difficulty labels map to (`run_settings`: the cells of the
`[profiles.NAME]` table that `inference_profile` names): the run command takes them as `{model}` and
`{effort}`, the profile's name as `{profile}` (with `{review_flag}` after it: ` --review-profile NAME` in a mixed start) and its `permission_mode` as `{permission}`, the run's share of pytest workers as `{workers_flag}` or `{workers_env}` (the run line then says `; pytest workers N`), and the orchestrator writes the run line
`Run: MODEL, effort EFFORT` on the issue when it starts the run. Each profile names its harness: the
`[harnesses.NAME]` table gives the text that starts the skill (`skill_prompt`, which `{prompt}` puts into the command) and
the words of a turn that stopped on an API error (`stop_patterns`, with a third field false for a cause that gets no retry). A profile whose harness takes no effort level or permission mode leaves it out. The orchestrator builds the command from `session_host` and the selected harness.
A cell may name another `[profiles.NAME]` table as its third field: that table's harness, provider, permission mode
and credential run the cell (`cell_config`), the run line names it (`; profile NAME`), and the local review stays the
profile in force's. The preflight checks the credential of each profile that a cell uses, and refuses a mix that the host
cannot run yet (`check_mix`).
With `{token}` in the start prompt, a start from Todo
writes `(start TOKEN)` after it and gives the session the same token: an issue in Todo whose newest run line has another
starter's token, younger than `started_elsewhere_seconds`, waits (`pending_start`).
Each round first writes its start beside the last plan (`mark_round_start`: `--watch` and `--check` show `round running for N min` while it is newer than the plan and its process, which started before the mark, lives), then its plan (`build_plan`: the counts, one queue per Area, the other waits, the events) to `state_file`.
The loop prints it as text, each event also as a JSON line, only when it changed or the round has an event, and
one `no change since` line after `HEARTBEAT_SECONDS` of quiet. `--watch` draws the state file's plan in
place, for a narrow pane (`render`); it reads only that file.
With `queue_issue` set, it also keeps the plan's queues in one comment on that issue, edited in place when they change and at
most once per `queue_write_minutes` (`PinnedComment`): the owner reads it in Linear's app on the phone.
After the starts, each round closes the herdr workspace (or the Paseo agent, and the Paseo workspace titled with its key) of a run whose issue is Done or Canceled for `close_after_hours`
and whose agent is idle or done (`to_close`): the transcript stays in Claude Code's own files, the decisions in
the issue and the pull request. Then it closes each open pull request whose issue is Canceled for `cancel_grace_minutes`,
with one comment (`to_drop`): the owner dropped the work, and Done means merged.

    python3 orchestrator/orchestrator.py --config orchestrator/orchestrator.toml [--once]
    python3 orchestrator/orchestrator.py --config orchestrator/orchestrator.toml --check
    python3 orchestrator/orchestrator.py --config orchestrator/orchestrator.toml --watch
    python3 orchestrator/orchestrator.py --config orchestrator/orchestrator.toml --log FILE

`--log FILE` appends each line of the loop to FILE and opens the file again for each line, so a rotation (the
service's newsyslog entry renames the file) costs no line: the next line goes to the new file, and `--check` reads it.
Without it the lines go to standard output.

`--check` prints one line per check, `ok`, `WARN` or `FAIL`, and exits 1 on a FAIL: the keys, an
answer from Linear and from GitHub, a filled example start command, the inference profile in
force and its key in the keychain, on Paseo each product's session endpoint (`check_endpoints`), the service (a round in `state_file` younger than three poll periods, no newer `waiting:` line in the log), the sessions that
still work after a day, and the commits of main that the installed copy lacks. It prints issue keys only, never a key or
an issue's text.

The config file names every setting: `load_config` has no default, and a missing or unknown key stops the start (exit 2)
with every missing key's line from the example, ready to paste.

Several products: the config's own tables are the first product (its team, repo and `run_cwd`), and each `[[product]]`
table adds one: `team_configs` makes a Config of each, `round_all` runs one round over them one after the other, each with its own
runner, and `max_runs` stays one number for all (`Runner.elsewhere`: the slots that the other teams' last plans held). A run opens
in its team's `run_cwd`; the repository, the closes and the escalations are the team's own.
Each product names its session endpoint (`Endpoint`: `paseo_host`, `paseo_password_item`, `cpus`): its sessions run in the
Paseo daemon at that address, in the product's own user or on another machine, and each `paseo` call for it goes there
(`PaseoHost.call`, `PaseoEndpoints`). A product with none of the three keys runs in the local daemon, as before. The state file is the first team's plan,
with the later teams' plans in `others`; one queue issue holds every team.

Keys come from the environment: one Linear key for each team and GH_TOKEN (read by `gh`). A team names the variable of its
key with `linear_key_env` (a Linear key reaches one workspace, so teams of two workspaces need two keys); the
default is LINEAR_API_KEY. `linear_api` gives the Linear module only the key of the team that it serves.
"""

from __future__ import annotations

import argparse
import importlib.util
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
import tempfile
import tomllib
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import Any, Protocol

# The states of a pull request (`pr_state`).
DRAFT = "draft"
WAITING = "waiting"  # ready for review, no verdict on its head yet
APPROVED = "approved"
CHANGES_REQUESTED = "changes requested"
FAILED = "failed"  # a required check failed, or the verdict check failed with no request for changes

BAD_CONCLUSIONS = {"FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE", "ERROR"}
PRIORITY_ORDER = {"urgent": 0, "high": 1, "medium": 2, "low": 3}
GH_FIELDS = "number,state,headRefName,isDraft,labels,reviews,statusCheckRollup,comments,commits"
KEY_SHAPE = re.compile(r"[A-Z][A-Z0-9]{0,9}-[0-9]{1,7}")  # a key goes into a shell command: nothing else may
MODES = ("start",)  # no "fix": the session that opened the pull request runs its fix round
HOSTS = ("herdr", "paseo", "process")
GH_TIMEOUT = 60
TOKEN_SHAPE = re.compile(r"[0-9]{8}T[0-9]{6}Z-[A-Za-z0-9.-]{1,63}")  # a start token (`start_token`) goes into a shell command too
SETTING_SHAPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/:@-]{0,63}")  # a model or an effort level goes into a shell command too; / : @ are no shell metacharacters (OpenRouter, Ollama, Vertex IDs)
# The permission modes of a session, the names that Claude Code's `--permission-mode` and Paseo's `--mode` both take
# (measured 2026-11-20, `paseo run --mode x` lists them). A session runs with no person to answer, so only these two can work:
# `auto` approves each action after a safety check that Claude Code runs only against Anthropic's own API, and
# `bypassPermissions` approves all.
PERMISSION_MODES = ("auto", "bypassPermissions")
CELLS = ("neither", "intricate", "judgment", "both")  # the cells of each [profiles.NAME] table, in the order of `run_settings`' index
# The first values of the table: Sonnet without `needs-judgment`, Opus with it; high effort with `intricate`.
# Full model IDs, as the harness reports them: an alias would put two versions in one group of `measure`.
DEFAULT_CELLS = (("claude-sonnet-5-5", "medium"), ("claude-sonnet-5-5", "high"), ("claude-opus-5-5", "medium"), ("claude-opus-5-5", "high"))


DEFAULT_LINEAR_KEY_ENV = "LINEAR_API_KEY"
KEY_ENV_SHAPE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")  # the name goes into the service's command line: nothing else may
# A product's session endpoint (`Endpoint`): the address that `paseo --host` takes (host:port, a socket, an SSH target), one
# word that is no option, and the keychain item of that daemon's password, which `security -s` takes.
PASEO_HOST_SHAPE = re.compile(r"[A-Za-z0-9\[/][^\s]{0,255}")
PASSWORD_ITEM_SHAPE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@:-]{0,127}")
PASSWORD_VARIABLE = "PASEO_PASSWORD"  # the variable that the paseo CLI reads the daemon's password from
LOCAL_DAEMON = "the local daemon"  # how `--check` names the endpoint of a product with no `paseo_host`


@dataclass(frozen=True)
class Endpoint:
    """Where a product's sessions run: the Paseo daemon at `host` (`paseo --host`; empty: the local daemon of the
    orchestrator's own user, as before), the keychain item in the orchestrator's user that holds that daemon's password
    (empty: none), and that machine's CPU count (0: the orchestrator's machine's)."""

    host: str = ""
    password_item: str = ""
    cpus: int = 0

    def name(self) -> str:
        return self.host or LOCAL_DAEMON


LOCAL = Endpoint()  # the local daemon of the orchestrator's own user: a product that names no `paseo_host`


class ConfigError(Exception):
    """The config file is missing or wrong."""


@dataclass(frozen=True)
class Product:
    """One more product that a round serves: its Linear team, its repository and the folder where its runs open.
    The states, the labels and every other setting come from the config's own tables, as they do for the first product.
    `areas`, `required_checks` and `conflict_author` are optional: None keeps the first product's. A public repository has
    its own app pair (agents/README.md "Setting up a product"), so its table names its own `conflict_author`."""

    team: str
    repo: str
    run_cwd: str
    areas: tuple[str, ...] | None = None
    required_checks: tuple[str, ...] | None = None
    linear_key_env: str = DEFAULT_LINEAR_KEY_ENV  # the environment variable of the team's Linear key
    conflict_author: str | None = None  # the implementer app's login in its repository; None keeps the first product's
    endpoint: Endpoint = LOCAL  # its session endpoint: with no `paseo_host`, the local daemon (never the first product's)


Harness = tuple[str, str, tuple[tuple[str, str, bool], ...]]  # (name, skill_prompt, stop_patterns) of a [harnesses.NAME] table


@dataclass(frozen=True)
class ProfileRun:
    """What another `[profiles.NAME]` table gives a cell that names it: the harness that runs the session, its
    permission mode, and the credential and its check. Its own cells and its review play no part."""

    key_check: str
    permission: str
    harness: Harness
    credential: str


@dataclass(frozen=True)
class Config:
    """Every setting in one place: the tool's own, then the names of the product it serves."""

    poll_seconds: int = 60
    max_runs: int = 3
    dry_run: bool = True
    claim_grace_seconds: int = 120
    claim_wait_seconds: int = 300
    started_elsewhere_seconds: int = 600  # a Todo issue whose newest run line has another start's token and is younger waits
    retry_wait_minutes: int = 3  # a turn that stopped on an API error gets one wake after this wait
    run_cwd: str = "."
    log_dir: str = "~/.local/state/orchestrator"
    state_file: str = "~/.local/state/orchestrator/state.json"
    run_command: str = ""  # selected by load_config, never read from TOML
    session_host: str = ""
    team: str = ""
    linear_script: str = ""
    linear_key_env: str = DEFAULT_LINEAR_KEY_ENV  # the variable of this team's Linear key; `linear_api` reads no other
    read_states: tuple[str, ...] = ()
    todo: str = "Todo"
    in_progress: str = "In Progress"
    escalated: str = "Escalated"
    stack_states: tuple[str, ...] = ()
    closed_states: tuple[str, ...] = ("Done", "Canceled", "Duplicate")
    skip_labels: tuple[str, ...] = ()
    restart_label: str = "restart"
    too_big_size: str = "L"
    areas: tuple[str, ...] = ()
    distinct_areas: bool = False
    review_states: tuple[str, ...] = ("In Review", "Approved")
    repo: str = ""
    required_checks: tuple[str, ...] = ()
    verdict_check: str = "verdict"
    conflict_comment: str = ""
    conflict_author: str = ""
    escalated_label: str = "escalated"
    log_file: str = ""  # the [service] table: what `--check` reads
    service_user: str = ""  # the agents' macOS user: the fix of a Paseo daemon on another profile names it
    install_dir: str = ""
    clone: str = ""
    queue_issue: str = ""  # the issue whose one comment holds the queue; empty: no queue is written
    halt_check_minutes: int = 5
    halt_notice_area: str = "orchestrator"
    queue_write_minutes: int = 5
    close_after_hours: int = 12  # a run's workspace closes this long after its issue became final; 0: never
    canceled: str = "Canceled"  # the state of an issue the owner dropped: its open pull request closes
    cancel_grace_minutes: int = 10  # a cancel younger than this keeps its pull request open: the owner may undo it
    judgment_label: str = "needs-judgment"  # the difficulty labels and the model and effort of each pair
    intricate_label: str = "intricate"
    run_cells: tuple[tuple[str, str], ...] = DEFAULT_CELLS  # in the order of CELLS: the cells of the inference profile in force
    inference_profile: str = "claude"  # the [profiles.NAME] table in force: `{profile}` gives its name to the session
    profile_key_check: str = ""  # its `key_check`: the command that `--check` runs to find the profile's credential
    credential: str = "keychain"  # what `key_check` finds: `keychain` (one of CREDENTIALS) or `sign-in`, for a profile that runs on a sign-in its harness keeps and renews itself
    permission_mode: str = "auto"  # its `permission_mode`: `{permission}` gives it to the session; one of PERMISSION_MODES, "" when its harness takes none
    review: tuple[str, str] = ("", "")  # its `review_model` and `review_effort`: the local review's model and effort level
    harness: str = "claude"  # the profile's `harness`: the [harnesses.NAME] table that gives the two settings below
    skill_prompt: str = "/implement {key} start {token}"  # the harness's text that starts the skill: `{prompt}` in run_command
    stop_patterns: tuple[tuple[str, str, bool], ...] = ()  # the harness's (pattern, cause, retry) triples of a turn that stopped on an API error
    cell_profiles: tuple[str, ...] = ()  # in the order of CELLS: the profile that runs each cell; empty: the profile in force runs all four
    cell_runs: tuple[tuple[str, ProfileRun], ...] = ()  # (name, settings) of each other profile that a cell names
    products: tuple[Product, ...] = ()  # the products after the first, from the `[[product]]` tables; `team_configs` makes one Config of each
    endpoint: Endpoint = LOCAL  # this product's session endpoint: `paseo_host`, `paseo_password_item` and `cpus`


# Every key of the config file: (table, key, kind, least). The table "" is the top of the file. Kinds: int (a whole
# number of `least` or more), bool, str, names (a list of strings), cell ([model, effort]). `load_config` has no default for any
# of them: a key that the file lacks stops the start, and a key that is not here stops it too. A test keeps this list equal to
# the keys of orchestrator.example.toml, which holds each key's first value.
SCHEMA: tuple[tuple[str, str, str, int], ...] = (
    ("", "poll_seconds", "int", 1), ("", "max_runs", "int", 1), ("", "dry_run", "bool", 0), ("", "distinct_areas", "bool", 0),
    ("", "claim_grace_seconds", "int", 0), ("", "claim_wait_seconds", "int", 0),
    ("", "started_elsewhere_seconds", "int", 0), ("", "retry_wait_minutes", "int", 0), ("", "queue_issue", "str", 0),
    ("", "halt_check_minutes", "int", 1), ("linear", "halt_notice_area", "str", 0),
    ("", "queue_write_minutes", "int", 0), ("", "close_after_hours", "int", 0), ("", "cancel_grace_minutes", "int", 0),
    ("", "session_host", "str", 0), ("", "run_cwd", "str", 0), ("", "log_dir", "str", 0),
    ("", "state_file", "str", 0), ("", "inference_profile", "str", 0),
    ("run_settings", "judgment_label", "str", 0), ("run_settings", "intricate_label", "str", 0),
    ("linear", "team", "str", 0), ("linear", "script", "str", 0), ("linear", "read_states", "names", 0), ("linear", "todo", "str", 0),
    ("linear", "in_progress", "str", 0), ("linear", "escalated", "str", 0), ("linear", "canceled", "str", 0),
    ("linear", "stack_states", "names", 0), ("linear", "closed_states", "names", 0), ("linear", "skip_labels", "names", 0),
    ("linear", "restart_label", "str", 0), ("linear", "too_big_size", "str", 0), ("linear", "areas", "names", 0),
    ("linear", "review_states", "names", 0),
    ("github", "repo", "str", 0), ("github", "required_checks", "names", 0), ("github", "verdict_check", "str", 0),
    ("github", "conflict_comment", "str", 0), ("github", "conflict_author", "str", 0), ("github", "escalated_label", "str", 0),
    ("service", "user", "str", 0), ("service", "install_dir", "str", 0), ("service", "python", "str", 0), ("service", "label", "str", 0),
    ("service", "log_file", "str", 0), ("service", "clone", "str", 0), ("service", "keychain_check", "str", 0),
)
# The keys that the file may leave out: the config of before them loads as it did. (table, key, kind, least), as SCHEMA.
OPTIONAL_SCHEMA: tuple[tuple[str, str, str, int], ...] = (
    ("linear", "linear_key_env", "str", 0),
    # the first product's session endpoint (`Endpoint`); a `[[product]]` table takes the same three keys
    ("", "paseo_host", "str", 0), ("", "paseo_password_item", "str", 0), ("", "cpus", "int", 1),
)
EXAMPLE = "orchestrator.example.toml"
ENDPOINT_KEYS = ("paseo_host", "paseo_password_item", "cpus")
# The keys of a `[[product]]` table: the first three are required, the next two are lists of names, then two texts and the
# three keys of its session endpoint.
PRODUCT_KEYS = ("team", "repo", "run_cwd", "areas", "required_checks", "linear_key_env", "conflict_author", *ENDPOINT_KEYS)
# The keys of each `[profiles.NAME]` table, all required: the four cells of `run_settings`, the command that
# finds the profile's credential, its harness, and the local review's model and effort level (one model
# for each profile, whatever the issue's labels). The provider's address and its key's item are `.workflow/agent_user.py`
# PROFILES.
# A profile may leave `permission_mode` out when its harness takes none. `credential` says what its `key_check`
# finds: a keychain item, or a sign-in that the harness keeps and renews itself.
PROFILE_KEYS = (*CELLS, "key_check", "harness", "provider", "review_model", "review_effort")
PROFILE_OPTIONAL = ("permission_mode", "credential")
# Where a profile's credential is: in the agent's keychain, or in a sign-in that the harness keeps and renews
# itself (pi's `/login` into ~/.pi/agent/auth.json): `--check` runs the profile's `key_check` either way, and says which.
CREDENTIALS = ("keychain", "sign-in")
# The keys of each `[harnesses.NAME]` table, all required: the text that starts the skill, with `{key}` and
# `{token}` (`{prompt}` in run_command), and the (pattern, cause, retry) triples of a stopped turn (`PaseoHost.paused`).
HARNESS_KEYS = ("skill_prompt", "stop_patterns")
PROMPT_PLACES = ("key", "token")


def _before_comment(line: str) -> str:
    """The line without its trailing comment (a # inside a quoted value stays)."""
    quote = ""
    for i, ch in enumerate(line):
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "\"'":
            quote = ch
        elif ch == "#":
            return line[:i].rstrip()
    return line.rstrip()


def example_lines(text: str) -> dict[tuple[str, str], str]:
    """The `key = value` line of each key of the example file, by (table, key), with no comment."""
    lines: dict[tuple[str, str], str] = {}
    table = ""
    for raw in text.splitlines():
        if header := re.match(r"\[([A-Za-z0-9_.@:/-]+)\]", raw):  # a table's name, a profile's too (`pi-ollama`: SETTING_SHAPE)
            table = header.group(1)
        elif found := re.match(r"([A-Za-z_]+)\s*=", raw):
            lines[(table, found.group(1))] = _before_comment(raw)
    return lines


def _example_text() -> str:
    """The example file beside this one (the clone has it; the installed copy has none, so there the message names only the key)."""
    try:
        return (Path(__file__).resolve().parent / EXAMPLE).read_text()
    except OSError:
        return ""


def load_config(path: Path) -> Config:
    """Read the TOML file, which names every setting; ConfigError names what is missing or unknown (all at once), and for a
    missing key the line to paste from the example."""
    try:
        data = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"cannot read {path}: {e}") from None
    if "run_command" in data:
        raise ConfigError(f"{path}: run_command is retired: `owlcode install` removes the line")
    extra = data.pop("product", [])  # the optional `[[product]]` tables: read after the schema check below
    profiles = data.pop("profiles", {})  # the `[profiles.NAME]` tables: read after the schema check below
    harnesses = data.pop("harnesses", {})  # the `[harnesses.NAME]` tables: read with the profiles
    tables = {name for name, *_ in SCHEMA if name}
    for name in tables:
        if name in data and not isinstance(data[name], dict):
            raise ConfigError(f"{path}: {name} must be a table")
    # An older copy of the config holds the retired key: loaded as it is, it would never see a conflict again, and say nothing.
    if "conflict_marker" in (data.get("github") or {}):
        raise ConfigError(f"{path}: [github] conflict_marker is retired: set conflict_comment and conflict_author instead (`owlcode install` removes the key and adds those two)")
    if moved := [name for name in CELLS if name in (data.get("run_settings") or {})]:  # a copy made before the [profiles.NAME] tables
        raise ConfigError(f"{path}: [run_settings] {', '.join(moved)} moved to the [profiles.NAME] tables: "
                          f"`owlcode install` removes them and adds inference_profile and the tables of {EXAMPLE}")

    def where(table: str, key: str) -> str:
        return f"[{table}] {key}" if table else key

    known = {(table, key) for table, key, _, _ in (*SCHEMA, *OPTIONAL_SCHEMA)}
    present = {("", key) for key in data if key not in tables}
    for table in tables & set(data):
        present |= {(table, key) for key in data[table]}
    unknown = sorted(present - known)
    missing = [(table, key) for table, key, _, _ in SCHEMA if (table, key) not in present]
    if unknown or missing:
        lines = example_lines(_example_text())
        problems = []
        if missing:
            pasted = [f"  {f'[{t}] ' if t else ''}{lines.get((t, k), f'{k} = ?  (see {EXAMPLE})')}" for t, k in missing]
            problems.append(f"missing in {path}:\n" + "\n".join(pasted))
        if unknown:
            problems.append(f"unknown in {path} (a misspelt key must not run on a default): " + ", ".join(where(t, k) for t, k in unknown))
        raise ConfigError("\n".join(problems))

    def value(table: str, key: str) -> Any:
        return (data[table] if table else data)[key]

    def key_env(table: dict[str, Any], where_: str) -> str:
        """The optional `linear_key_env` of a table: the name of an environment variable, LINEAR_API_KEY when it is left out."""
        found = table.get("linear_key_env", DEFAULT_LINEAR_KEY_ENV)
        if not isinstance(found, str) or not KEY_ENV_SHAPE.fullmatch(found):
            raise ConfigError(f"{path}: {where_} linear_key_env must be the name of an environment variable (capital letters, digits "
                              "and _, a letter first), such as LINEAR_API_KEY")
        return found

    def whole(table: str, key: str, least: int) -> int:
        v = value(table, key)
        if not isinstance(v, int) or isinstance(v, bool) or v < least:
            raise ConfigError(f"{path}: {where(table, key)} must be a whole number, {least} or more")
        return v

    def flag(key: str) -> bool:
        v = value("", key)
        if not isinstance(v, bool):
            raise ConfigError(f"{path}: {key} must be true or false")
        return v

    def text(table: str, key: str) -> str:
        v = value(table, key)
        if not isinstance(v, str):
            raise ConfigError(f"{path}: {where(table, key)} must be text")
        return v

    def names(table: str, key: str) -> tuple[str, ...]:
        v = value(table, key)
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            raise ConfigError(f"{path}: {where(table, key)} must be a list of names")
        return tuple(v)

    for table, key in (("linear", "team"), ("github", "repo")):
        if not text(table, key):
            raise ConfigError(f"{path}: [{table}] {key} is empty")
    host = text("", "session_host")
    if host not in HOSTS:
        raise ConfigError(f"{path}: session_host must be one of {', '.join(HOSTS)}")
    queue_issue = text("", "queue_issue")
    if queue_issue and not KEY_SHAPE.fullmatch(queue_issue):
        raise ConfigError(f"{path}: queue_issue must be an issue key, such as ABC-12")
    if bool(text("github", "conflict_comment")) != bool(text("github", "conflict_author")):
        raise ConfigError(f"{path}: [github] conflict_comment and conflict_author go together: set both, or neither")
    profile = text("", "inference_profile")
    cells, key_check, permission, harness, credential, review, cell_profiles, cell_runs = _profile(
        path, profiles, profile, _harnesses(path, harnesses))
    # The command of the profile in force takes an effort level when its own cells name one (a cell on another profile
    # gets its own command at its start: `cell_config`).
    own_effort = next((bool(effort) for (_, effort), name in zip(cells, cell_profiles, strict=True) if name == profile), True)
    areas = names("linear", "areas")
    distinct = flag("distinct_areas")
    if distinct and not areas:
        raise ConfigError(f"{path}: distinct_areas is on, but [linear] areas is empty: list the Area labels, or set distinct_areas = false")
    products = _products(path, extra, text("linear", "team"), areas, distinct, key_env)
    endpoint, products = same_endpoints(path, endpoint_of(path, data, ""), products)
    return Config(
        poll_seconds=whole("", "poll_seconds", 1), max_runs=whole("", "max_runs", 1), dry_run=flag("dry_run"),
        run_command=run_template(host, harness[0], path, effort=own_effort), session_host=host,
        claim_grace_seconds=whole("", "claim_grace_seconds", 0), claim_wait_seconds=whole("", "claim_wait_seconds", 0),
        started_elsewhere_seconds=whole("", "started_elsewhere_seconds", 0),
        retry_wait_minutes=whole("", "retry_wait_minutes", 0),
        escalated=text("linear", "escalated"), escalated_label=text("github", "escalated_label"),
        run_cwd=text("", "run_cwd"), log_dir=text("", "log_dir"), state_file=text("", "state_file"),
        team=text("linear", "team"), linear_script=text("linear", "script"),
        linear_key_env=key_env(data.get("linear") or {}, "[linear]"),
        read_states=names("linear", "read_states"), todo=text("linear", "todo"), in_progress=text("linear", "in_progress"),
        stack_states=names("linear", "stack_states"), closed_states=names("linear", "closed_states"),
        skip_labels=names("linear", "skip_labels"), restart_label=text("linear", "restart_label"),
        too_big_size=text("linear", "too_big_size"), areas=areas, distinct_areas=distinct,
        review_states=names("linear", "review_states"), repo=text("github", "repo"),
        required_checks=names("github", "required_checks"), verdict_check=text("github", "verdict_check"),
        conflict_comment=text("github", "conflict_comment"), conflict_author=text("github", "conflict_author"),
        log_file=text("service", "log_file"), service_user=text("service", "user"),
        install_dir=text("service", "install_dir"), clone=text("service", "clone"),
        queue_issue=queue_issue, queue_write_minutes=whole("", "queue_write_minutes", 0),
        halt_check_minutes=whole("", "halt_check_minutes", 1), halt_notice_area=text("linear", "halt_notice_area"),
        close_after_hours=whole("", "close_after_hours", 0), canceled=text("linear", "canceled"),
        cancel_grace_minutes=whole("", "cancel_grace_minutes", 0),
        judgment_label=text("run_settings", "judgment_label"), intricate_label=text("run_settings", "intricate_label"), run_cells=cells,
        inference_profile=profile, profile_key_check=key_check, permission_mode=permission, credential=credential,
        review=review, products=products, endpoint=endpoint,
        harness=harness[0], skill_prompt=harness[1], stop_patterns=harness[2],
        cell_profiles=cell_profiles if any(name != profile for name in cell_profiles) else (), cell_runs=cell_runs,
    )


def _harnesses(path: Path, found: object) -> dict[str, Harness]:
    """The `[harnesses.NAME]` tables: each harness's text that starts the skill and its stop patterns. Every
    table is checked: a profile switch is one line, so the other harnesses must load too."""
    if not isinstance(found, dict) or not found or not all(isinstance(t, dict) for t in found.values()):
        raise ConfigError(f"{path}: no [harnesses.NAME] tables: add those of {EXAMPLE}")
    lines = example_lines(_example_text())
    read: dict[str, Harness] = {}
    for harness, table in found.items():
        where = f"[harnesses.{harness}]"
        if not SETTING_SHAPE.fullmatch(harness):
            raise ConfigError(f"{path}: {where}: a harness's name is letters, digits, . _ - / : or @")
        missing = [k for k in HARNESS_KEYS if k not in table]
        unknown = sorted(set(table) - set(HARNESS_KEYS))
        if missing or unknown:
            pasted = [f"  {where} {lines.get((f'harnesses.{harness}', k), f'{k} = ?  (see {EXAMPLE})')}" for k in missing]
            raise ConfigError("\n".join(([f"missing in {path}:", *pasted] if missing else [])
                                        + ([f"unknown in {path}: " + ", ".join(f"{where} {k}" for k in unknown)] if unknown else [])))
        prompt = table["skill_prompt"]
        if (not isinstance(prompt, str) or "{key}" not in prompt
                or set(re.findall(r"\{([^{}]*)\}", prompt)) - set(PROMPT_PLACES) or "\n" in prompt):
            raise ConfigError(f"{path}: {where} skill_prompt must be one line of text with {{key}}, and no place but {{key}} and {{token}}")
        stops = table["stop_patterns"]
        if not isinstance(stops, list) or not all(isinstance(x, list) and len(x) in (2, 3)
                                                  and all(isinstance(v, str) and v for v in x[:2])
                                                  and (len(x) == 2 or isinstance(x[2], bool)) for x in stops):
            raise ConfigError(f"{path}: {where} stop_patterns must be a list of [pattern, cause] pairs of text, "
                              "with false after a cause that gets no retry")
        for pattern, *_ in stops:
            try:
                re.compile(pattern)
            except re.error as e:
                raise ConfigError(f"{path}: {where} stop_patterns: {pattern!r} is no regular expression ({e})") from None
        read[harness] = (harness, prompt, tuple((x[0], x[1], x[2] if len(x) == 3 else True) for x in stops))
    return read


def _model_catalog() -> dict[str, Any]:
    path = Path(__file__).with_name("model-catalog.json")
    try:
        catalog = json.loads(path.read_text())
        if not isinstance(catalog, dict) or not all(isinstance(catalog.get(key), dict) for key in ("profiles", "providers", "efforts")):
            raise ValueError("expected profiles, providers and efforts objects")
    except (OSError, ValueError) as e:
        raise ConfigError(f"cannot read {path}: {e}") from e
    return catalog


Route = tuple[dict[str, dict[str, Any]], str]  # (the models a profile may name with their effort levels, the provider prefix)


def _require(where: str, field: str, value: object, valid: Iterable[str]) -> None:
    choices = list(valid)
    if value not in choices:
        allowed = ", ".join(choice or "(omit)" for choice in choices) or "(none)"
        raise ConfigError(f"{where} {field} {value!r} is not in the model catalog: one of {allowed}")


def _catalog_route(where: str, catalog: dict[str, Any], profile: str, harness: str, provider: object,
                   permission: str) -> Route:
    """Check a profile's harness, provider and permission mode against the catalog, and return the models of its provider
    on its harness and the prefix that the harness needs at runtime (pi's provider: the
    profile's name, under which `agent_user.py user` writes its provider)."""
    routes = catalog["profiles"]
    _require(where, "profile", profile, routes)
    route = routes[profile]
    _require(where, "harness", harness, [route["harness"]])
    _require(where, "provider", provider, [route["provider"]])
    provider_data = catalog["providers"][route["provider"]]
    connection = provider_data["connections"][harness]
    _require(where, "permission_mode", permission, connection["permissions"] or [""])
    models = {model: catalog["efforts"][entry["efforts"][harness]] for model, entry in provider_data["models"].items()
              if harness in entry["efforts"]}
    prefix = (connection.get("sign_in") or profile) + "/" if harness == "pi" else ""
    return models, prefix


def _catalog_cell(where: str, route: Route, model_field: str, effort_field: str, model: str, effort: str) -> str:
    """Check one model and its effort level against a profile's route, and return the model's name as the harness sees it."""
    models, prefix = route
    if model not in models:
        head, slash, bare = model.partition("/")
        if slash and (head + slash == prefix or bare in models):
            raise ConfigError(f'{where} {model_field.removesuffix(" model")}: write the model without "{head}/": {bare}')
    _require(where, model_field, model, models)
    _require(where, effort_field, effort, (level for level, sent in models[model].items() if sent is not None))
    return prefix + model


Cell = tuple[str, str, str]  # (model, effort, the profile that runs it) as the table writes it


def _cell(path: Path, where: str, cell: str, value: object, own: str, profiles: Collection[str]) -> Cell:
    """One cell as the table writes it: [model, effort], or [model] when the harness takes no effort level, and
    either with the name of another `[profiles.NAME]` table last: that table's provider and harness run the cell. A second
    field that names a profile is a profile, else an effort level."""
    if (not isinstance(value, list) or len(value) not in (1, 2, 3)
            or not all(isinstance(x, str) and SETTING_SHAPE.fullmatch(x) for x in value)):
        raise ConfigError(f"{path}: {where} {cell} must be [model, effort] (or [model] when the harness takes no effort), "
                          "then the name of another profile when that profile runs the cell, "
                          "each a name of letters, digits, . _ - / : or @")
    if len(value) == 3 or (len(value) == 2 and value[1] in profiles):
        model, effort, other = (value[0], "", value[1]) if len(value) == 2 else value
        if other == own:
            raise ConfigError(f"{path}: {where} {cell} names its own table {other!r}: leave the third field out")
        if other not in profiles:
            raise ConfigError(f"{path}: {where} {cell}: {other!r} names no [profiles.{other}] table: one of "
                              f"{', '.join(sorted(set(profiles) - {own}))}")
        return model, effort, other
    return value[0], value[1] if len(value) == 2 else "", own


Profile = tuple[tuple[tuple[str, str], ...], str, str, Harness, str, tuple[str, str], tuple[str, ...],
                tuple[tuple[str, ProfileRun], ...]]


def _profile(path: Path, found: object, name: str, harnesses: Mapping[str, Harness]) -> Profile:
    """The cells, the key check, the permission mode, the harness, the credential and the local review's (model, effort) of
    the profile `inference_profile` names, then the profile that runs each
    cell and the settings of each other profile that a cell names. Every `[profiles.NAME]` table is checked, not
    only the one in force: a switch is one line, so the other profiles must load too. A missing key is named with the
    example's line, as `load_config` names one. A cell is [model, effort], or [model] when the harness takes no effort level
    (all cells on one profile alike), with the name of another profile last when that profile runs it (`_cell`): its model
    and effort are checked against that profile's provider and harness, and get that provider's prefix. Only one level: the
    named table's own cells play no part. `permission_mode` is optional ("" when it is left out), and so is `credential`
    (`keychain`). The file uses catalog model IDs for runs and reviews. This function returns the names the harness sees,
    including the provider prefix for pi."""
    if isinstance(found, dict) and "ollama" in found:
        raise ConfigError(f"{path}: remove [profiles.ollama]; use [profiles.pi-ollama] instead: `owlcode install` does it")
    if name == "ollama":
        raise ConfigError(f'{path}: set inference_profile = "pi-ollama" instead of "ollama": `owlcode install` does it')
    if not isinstance(found, dict) or not found or not all(isinstance(t, dict) for t in found.values()):
        raise ConfigError(f"{path}: no [profiles.NAME] tables: add those of {EXAMPLE}")
    lines = example_lines(_example_text())
    catalog = _model_catalog()
    runs: dict[str, ProfileRun] = {}
    routes: dict[str, Route] = {}
    for profile, table in found.items():  # first each table's own settings: a cell may name a table that comes later
        where = f"[profiles.{profile}]"
        if not SETTING_SHAPE.fullmatch(profile):
            raise ConfigError(f"{path}: {where}: a profile's name is letters, digits, . _ - / : or @")
        missing = [k for k in PROFILE_KEYS if k not in table]
        unknown = sorted(set(table) - set(PROFILE_KEYS) - set(PROFILE_OPTIONAL))
        if missing or unknown:
            pasted = [f"  {where} {lines.get((f'profiles.{profile}', k), f'{k} = ?  (see {EXAMPLE})')}" for k in missing]
            raise ConfigError("\n".join(([f"missing in {path}:", *pasted] if missing else [])
                                        + ([f"unknown in {path}: " + ", ".join(f"{where} {k}" for k in unknown)] if unknown else [])))
        if not isinstance(table["key_check"], str) or not table["key_check"].strip():
            raise ConfigError(f"{path}: {where} key_check must be a command")
        permission = table.get("permission_mode", "")
        credential = table.get("credential", CREDENTIALS[0])
        if credential not in CREDENTIALS:
            raise ConfigError(f"{path}: {where} credential must be one of {', '.join(CREDENTIALS)}")
        for key in ("review_model", "review_effort"):
            if not isinstance(table[key], str) or not SETTING_SHAPE.fullmatch(table[key]):
                raise ConfigError(f"{path}: {where} {key} must be a model's or an effort level's name: letters, digits, . _ - / : "
                                  "or @")
        harness = table["harness"]
        routes[profile] = _catalog_route(f"{path}: {where}", catalog, profile, harness, table["provider"], permission)
        if harness not in harnesses:
            raise ConfigError(f"{path}: {where} harness {harness!r} needs a [harnesses.{harness}] table (see {EXAMPLE})")
        runs[profile] = ProfileRun(table["key_check"], permission, harnesses[harness], credential)
    read: dict[str, Profile] = {}
    for profile, table in found.items():
        where = f"{path}: [profiles.{profile}]"
        cells = [_cell(path, f"[profiles.{profile}]", cell, table[cell], profile, found) for cell in CELLS]
        for runner in {other for _, _, other in cells}:
            if len({bool(effort) for _, effort, other in cells if other == runner}) > 1:
                raise ConfigError(f"{where}: every cell on one profile names an effort level, or none does")
        run_cells = tuple((_catalog_cell(where if other == profile else f"{where} (on [profiles.{other}])", routes[other],
                                         f"{cell} model", f"{cell} effort", model, effort), effort)
                          for cell, (model, effort, other) in zip(CELLS, cells, strict=True))
        review = (_catalog_cell(where, routes[profile], "review_model", "review_effort", table["review_model"],
                                table["review_effort"]), table["review_effort"])
        own = runs[profile]
        used = tuple(dict.fromkeys(other for _, _, other in cells if other != profile))
        read[profile] = (run_cells, own.key_check, own.permission, own.harness, own.credential, review,
                         tuple(other for _, _, other in cells), tuple((other, runs[other]) for other in used))
    if name not in read:
        raise ConfigError(f"{path}: inference_profile {name!r} has no [profiles.{name}] table: one of {', '.join(sorted(read))}")
    return read[name]


def _products(path: Path, found: object, first: str, areas: tuple[str, ...], distinct: bool,
              key_env: Callable[[dict[str, Any], str], str]) -> tuple[Product, ...]:
    """The `[[product]]` tables as `Product`s: each names its team, repo and run_cwd (areas, required_checks, linear_key_env,
    conflict_author and the three keys of its session endpoint are optional), and no key besides; no team twice, and none
    the first product's."""
    if not isinstance(found, list) or not all(isinstance(x, dict) for x in found):
        raise ConfigError(f"{path}: product must be a list of tables: write each as [[product]]")
    out: list[Product] = []
    for table in found:
        unknown = sorted(set(table) - set(PRODUCT_KEYS))
        missing = [k for k in PRODUCT_KEYS[:3] if k not in table]
        if unknown or missing:
            raise ConfigError(f"{path}: [[product]] " + "; ".join(
                part for part in (f"missing {', '.join(missing)}" if missing else "", f"unknown {', '.join(unknown)}" if unknown else "") if part))
        for key in PRODUCT_KEYS[:3]:
            if not isinstance(table[key], str) or not table[key]:
                raise ConfigError(f"{path}: [[product]] {key} must be text and not empty")
        lists: dict[str, tuple[str, ...] | None] = {}
        for key in PRODUCT_KEYS[3:5]:
            v = table.get(key)
            if v is not None and (not isinstance(v, list) or not all(isinstance(x, str) for x in v)):
                raise ConfigError(f"{path}: [[product]] {key} must be a list of names")
            lists[key] = tuple(v) if v is not None else None
        if distinct and not (areas if lists["areas"] is None else lists["areas"]):
            raise ConfigError(f"{path}: distinct_areas is on, but the areas of [[product]] {table['team']} are empty: list the Area labels")
        author = table.get("conflict_author")
        if author is not None and (not isinstance(author, str) or not author):
            raise ConfigError(f"{path}: [[product]] conflict_author must be text and not empty: the implementer app's login")
        out.append(Product(table["team"], table["repo"], table["run_cwd"], lists["areas"], lists["required_checks"],
                           key_env(table, f"[[product]] {table['team']}"), author,
                           endpoint_of(path, table, f"[[product]] {table['team']} ")))
    teams = [first, *(p.team for p in out)]
    if len({t.upper() for t in teams}) != len(teams):
        raise ConfigError(f"{path}: a team appears twice among [linear] team and the [[product]] tables")
    return tuple(out)


def endpoint_of(path: Path, table: Mapping[str, Any], where: str) -> Endpoint:
    """The session endpoint that a table names (`paseo_host`, `paseo_password_item`, `cpus`, each optional): with none of
    them, the local daemon, as before. The address and the item's name go into a command line, so each has its shape; the
    password itself is only in the keychain."""
    host, item, cpus = table.get("paseo_host", ""), table.get("paseo_password_item", ""), table.get("cpus", 0)
    if not isinstance(host, str) or (host and not PASEO_HOST_SHAPE.fullmatch(host)):
        raise ConfigError(f"{path}: {where}paseo_host must be text: the daemon's address as `paseo --host` takes it "
                          "(host:port), one word that does not start with -")
    if not isinstance(item, str) or (item and not PASSWORD_ITEM_SHAPE.fullmatch(item)):
        raise ConfigError(f"{path}: {where}paseo_password_item must be text: the name of a keychain item (letters, digits and . _ @ : -)")
    if item and not host:
        raise ConfigError(f"{path}: {where}paseo_password_item needs paseo_host: the local daemon takes no password")
    if "cpus" in table and (not isinstance(cpus, int) or isinstance(cpus, bool) or cpus < 1):
        raise ConfigError(f"{path}: {where}cpus must be a whole number, 1 or more: the CPU count of the machine of its sessions")
    return Endpoint(host, item, cpus)


def same_endpoints(path: Path, first: Endpoint, products: Sequence[Product]) -> tuple[Endpoint, tuple[Product, ...]]:
    """One endpoint, one password item and one CPU count: the products of one `paseo_host` (no `paseo_host`: the local
    daemon) that name `paseo_password_item` or `cpus` name the same value, and a product that leaves one out takes the
    value that another product of its endpoint names."""
    named: dict[str, Endpoint] = {}
    for found in (first, *(p.endpoint for p in products)):
        had = named.get(found.host, Endpoint(found.host))
        for attr, key in (("password_item", "paseo_password_item"), ("cpus", "cpus")):
            if getattr(found, attr) and getattr(had, attr) and getattr(found, attr) != getattr(had, attr):
                raise ConfigError(f"{path}: two tables of the endpoint {found.name()} name two values of {key}: one machine has one")
        named[found.host] = Endpoint(found.host, had.password_item or found.password_item, had.cpus or found.cpus)
    return named[first.host], tuple(replace(p, endpoint=named[p.endpoint.host]) for p in products)


def team_configs(cfg: Config) -> list[Config]:
    """One Config for each product the round serves, the first product's (the config's own tables) first. A later product
    has its own team, repo and run_cwd (and areas, required_checks and conflict_author when it names them), its own session
    endpoint (the local daemon when it names no `paseo_host`, never the first product's), its own Linear key's variable (the
    default, never the first team's: a Linear key reaches one workspace only), no `queue_issue` (one queue holds all teams, in the first team's workspace)
    and no products of its own; every other setting is shared, `max_runs` too: the round never runs more sessions than one
    team did."""
    return [cfg] + [replace(cfg, team=p.team, repo=p.repo, run_cwd=p.run_cwd, linear_key_env=p.linear_key_env,
                            areas=cfg.areas if p.areas is None else p.areas,
                            required_checks=cfg.required_checks if p.required_checks is None else p.required_checks,
                            conflict_author=cfg.conflict_author if p.conflict_author is None else p.conflict_author,
                            endpoint=p.endpoint, queue_issue="", products=()) for p in cfg.products]


# -- Pure decisions: no I/O below this line until "Reads". ---------------------------------------------------------------


def _check_name(check: Mapping[str, Any]) -> str:
    return str(check.get("name") or check.get("context") or "")


def _check_bad(check: Mapping[str, Any]) -> bool:
    """A finished check that did not pass. A check still running, or a skipped one, is not bad here."""
    verdict = str(check.get("conclusion") or check.get("state") or "").upper()
    return verdict in BAD_CONCLUSIONS


def _check_passed(check: Mapping[str, Any]) -> bool:
    return str(check.get("conclusion") or check.get("state") or "").upper() in {"SUCCESS", "NEUTRAL"}


def newest_checks(checks: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The newest run of each check name (the same rule as .github/branch_update.py `newest`). GitHub keeps a
    run that a newer one replaced (a cancelled run of a concurrency group, a failed `verdict` beside the rerun's) on the head.
    `gh` gives no run id, so the order is `startedAt`, and a run that has not finished counts as the newest. A skipped run
    replaces only a cancelled one: it must not hide a failure."""
    runs: dict[str, Mapping[str, Any]] = {}
    for check in sorted(checks, key=lambda c: (str(c.get("status") or "COMPLETED").upper() != "COMPLETED", str(c.get("startedAt") or ""))):
        name = _check_name(check)
        old = runs.get(name)
        skipped = str(check.get("conclusion") or "").upper() == "SKIPPED"
        if old is None or not skipped or str(old.get("conclusion") or "").upper() == "CANCELLED":
            runs[name] = check
    return list(runs.values())


def latest_review(pr: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The newest review that states a verdict (approved or changes requested); comments and dismissals say none."""
    given = [r for r in pr.get("reviews") or [] if str(r.get("state", "")).upper() in {"APPROVED", "CHANGES_REQUESTED"}]
    return max(given, key=lambda r: str(r.get("submittedAt") or "")) if given else None


def pr_state(pr: Mapping[str, Any], cfg: Config) -> str:
    """One of DRAFT, WAITING, APPROVED, CHANGES_REQUESTED, FAILED, from the artifacts of the pull request only."""
    if pr.get("isDraft"):
        return DRAFT
    checks = newest_checks(pr.get("statusCheckRollup") or [])
    review = latest_review(pr)
    asked = review is not None and str(review.get("state", "")).upper() == "CHANGES_REQUESTED"
    for check in checks:
        name = _check_name(check)
        if name in cfg.required_checks and _check_bad(check):
            return FAILED
    verdict = [c for c in checks if _check_name(c) == cfg.verdict_check]
    if any(_check_bad(c) for c in verdict):
        return CHANGES_REQUESTED if asked else FAILED
    if asked:
        return CHANGES_REQUESTED
    if review is not None and any(_check_passed(c) for c in verdict):
        return APPROVED
    return WAITING


def open_blockers(issue: Mapping[str, Any], cfg: Config) -> list[Mapping[str, Any]]:
    """The issues that block this one and are not closed."""
    return [b for b in issue.get("blocked_by") or [] if b.get("state") not in cfg.closed_states]


def can_start(issue: Mapping[str, Any], cfg: Config) -> bool:
    """agents/README.md "What can start now": in Todo, none of the owner's labels, no open blocker, not too big."""
    labels = set(issue.get("labels") or [])
    return (issue.get("state") == cfg.todo and not labels & set(cfg.skip_labels) and not open_blockers(issue, cfg)
            and issue.get("size") != cfg.too_big_size)


def stack_blocker(issue: Mapping[str, Any], cfg: Config, open_pr_keys: set[str]) -> str | None:
    """The key of the one open blocker that lets the issue start as a draft stack, else None. The issue must meet every
    other rule of `can_start`; its only open blocker is in review (or approved) with its pull request open."""
    blockers = open_blockers(issue, cfg)
    if len(blockers) != 1:
        return None
    rest = dict(issue, blocked_by=[])
    blocker = blockers[0]
    if can_start(rest, cfg) and blocker.get("state") in cfg.stack_states and str(blocker.get("key")) in open_pr_keys:
        return str(blocker["key"])
    return None


def is_conflict_comment(comment: Mapping[str, Any], cfg: Config) -> bool:
    """The branch update's own comment: its author's login and its first words, as the review workflow counts it
    (agents/README.md "Review rounds"). A person's comment that only names a conflict does not count."""
    login = str((comment.get("author") or {}).get("login") or "")
    return bool(cfg.conflict_comment and cfg.conflict_author and login == cfg.conflict_author
                and str(comment.get("body") or "").startswith(cfg.conflict_comment))


def conflict_after_last_commit(pr: Mapping[str, Any], cfg: Config) -> bool:
    """The branch update's comment is newer than the branch's newest commit: the conflict is still open."""
    commits = [str(c.get("committedDate") or "") for c in pr.get("commits") or []]
    last = max(commits) if commits else ""
    return any(is_conflict_comment(c, cfg) and str(c.get("createdAt") or "") > last for c in pr.get("comments") or [])


def can_resume(issue: Mapping[str, Any], pr: Mapping[str, Any] | None, cfg: Config) -> str | None:
    """Why an issue in In Progress resumes, or None. Its pull request is ready for review and its latest review asks for
    changes, or a required check failed, or the branch update found a conflict; or the issue carries the restart label."""
    if issue.get("state") != cfg.in_progress or set(issue.get("labels") or []) & set(cfg.skip_labels):
        return None  # the owner's own work (`hands-off`) is never resumed (README "What can start now")
    if cfg.restart_label in (issue.get("labels") or []):
        return "restart label"
    return fix_reason(pr, cfg) if pr is not None else None


def fix_reason(pr: Mapping[str, Any], cfg: Config) -> str | None:
    """Why a pull request waits for a fix, whatever its issue's state: `can_resume`'s words, or None."""
    state = pr_state(pr, cfg)
    if state == DRAFT:
        return None
    if state in (CHANGES_REQUESTED, FAILED):
        return state
    if conflict_after_last_commit(pr, cfg):
        return "conflict with main"
    return None


def pr_for(key: str, prs: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The open pull request whose branch name carries the issue's key (Linear links by the key, in lower case)."""
    pattern = re.compile(r"(?<![a-z0-9])" + re.escape(key.lower()) + r"(?![0-9])")
    found = [p for p in prs if pattern.search(str(p.get("headRefName", "")).lower())]
    return max(found, key=lambda p: int(p.get("number") or 0)) if found else None


def _order(issue: Mapping[str, Any]) -> tuple[int, int, float, int]:
    """Priority (no priority last), then the owner's order in Linear (`manual_order`, the lower first: the owner drags the
    issues in a view ordered by priority), then the issue's number. An issue without the field (a read from before
    it) comes after the ones with it. The size does not count."""
    number = re.search(r"(\d+)$", str(issue.get("identifier", "")))
    manual = issue.get("manual_order")
    placed = float(manual) if isinstance(manual, int | float) and not isinstance(manual, bool) else None
    return (PRIORITY_ORDER.get(str(issue.get("priority")), 4), 1 if placed is None else 0, placed or 0.0,
            int(number.group(1)) if number else 0)


def open_pr_keys(issues: Sequence[Mapping[str, Any]], prs: Sequence[Mapping[str, Any]]) -> set[str]:
    return {str(i["identifier"]) for i in issues if pr_for(str(i["identifier"]), prs) is not None}


def stack_pairs(issues: Sequence[Mapping[str, Any]], prs: Sequence[Mapping[str, Any]], cfg: Config) -> list[tuple[str, str]]:
    """Each issue that can start as a draft stack, with its blocker. The orchestrator does not start a stack."""
    with_pr = open_pr_keys(issues, prs)
    return [(str(i["identifier"]), on) for i in sorted(issues, key=_order)
            if not can_start(i, cfg) and i.get("state") == cfg.todo and (on := stack_blocker(i, cfg, with_pr))]



ORIGIN = re.compile(r"^(?:Found in|From:)\W*([A-Z][A-Z0-9]{0,9}-[0-9]{1,7})")
ORIGIN_PR = re.compile(r"^From:\s*PR #([0-9]+)")


def origin_key(issue: Mapping[str, Any]) -> str | None:
    """Where this issue came from, by its first line (agents/README.md): `Found in KEY` or `From: KEY` gives the key, and a
    follow-up's `From: PR #N, round R, ...` gives `#N`, the pull request whose issue it came from."""
    first = str(issue.get("description") or "").strip().splitlines()[:1]
    if not first:
        return None
    if found := ORIGIN.match(first[0]):
        return found.group(1)
    found = ORIGIN_PR.match(first[0])
    return f"#{found.group(1)}" if found else None


def origin_prs(issue: Mapping[str, Any]) -> list[int]:
    """The pull requests that a follow-up came from: the `From: PR #N` lines at the top of its text, one after another. A
    collecting follow-up holds one such line for each review that added findings to it."""
    numbers: list[int] = []
    for line in str(issue.get("description") or "").strip().splitlines():
        if not (found := ORIGIN_PR.match(line)):
            break
        numbers.append(int(found.group(1)))
    return numbers


def unmerged_origin(issue: Mapping[str, Any], prs: Sequence[Mapping[str, Any]]) -> int | None:
    """The number of an open pull request that a follow-up came from (a `From: PR #N` line at its top, `origin_prs`), else
    None. The reviewer files a follow-up with the approve, before the merge, and its work needs the merged change: such an
    issue starts only when none of its pull requests is open (one started on a main without the change and
    stopped; a collecting issue waits for each of its origins)."""
    open_numbers = {int(p.get("number") or 0) for p in prs}
    return next((n for n in origin_prs(issue) if n in open_numbers), None)


def merge_wait(number: int) -> str:
    """Why a follow-up of an open pull request waits (`unmerged_origin`)."""
    return f"after the merge of PR #{number}"


def key_in(text: str) -> str | None:
    """The issue key in a branch name (`abc-12-short-name` gives `ABC-12`)."""
    found = re.search(r"(?<![a-z0-9])([a-z][a-z0-9]{0,9}-[0-9]{1,7})(?![0-9])", text.lower())
    return found.group(1).upper() if found else None


def own_area(labels: Sequence[str], cfg: Config) -> str | None:
    named = [label for label in labels if label in cfg.areas]
    return named[0] if len(named) == 1 else None


def area_of(issue: Mapping[str, Any], cfg: Config, origin_areas: Mapping[str, str | None]) -> str | None:
    """The issue's Area label, or the Area of the issue it came from (an old follow-up has none: agents/README.md)."""
    mine = own_area(issue.get("labels") or [], cfg)
    if mine is not None:
        return mine
    origin = origin_key(issue)
    return origin_areas.get(origin) if origin else None


def related_keys(issue: Mapping[str, Any]) -> set[str]:
    return {str(r.get("key")) for r in issue.get("related") or []}


def pick_starts(candidates: Sequence[Mapping[str, Any]], running: Sequence[Mapping[str, Any]], free: int,
                distinct_areas: bool, area: Callable[[Mapping[str, Any]], str | None],
                waiting: Sequence[Mapping[str, Any]] = ()) -> list[tuple[str, str | None]]:
    """What starts: for each candidate, in the order of `_order`, its key and None when it starts, or the reason it waits. A
    candidate waits when it has a related link to an issue that runs, that starts now or that waits for the owner with its
    pull request open (`waiting`: the planner's statement that the two touch the same files), when `distinct_areas` is on
    and its Area is one that runs or is unknown (off since the owner's call of 2026-11-16), or when no slot is free. An issue in `waiting` holds no Area (the owner's call, 2026-11-10)."""
    taken = list(running)
    out: list[tuple[str, str | None]] = []
    for issue in sorted(candidates, key=_order):
        key = str(issue["identifier"])
        mine = area(issue)
        near = [*taken, *waiting]
        near_keys = {str(t["identifier"]) for t in near}
        linked = sorted((related_keys(issue) & near_keys) | {str(t["identifier"]) for t in near if key in related_keys(t)})
        same = sorted(str(t["identifier"]) for t in taken if mine is not None and area(t) == mine)
        if linked:
            reason = "which runs" if linked[0] in {str(t["identifier"]) for t in taken} else "which waits for the owner"
            out.append((key, f"related to {linked[0]}, {reason}"))
        elif distinct_areas and mine is None:
            out.append((key, "no Area label: add one"))
        elif distinct_areas and same:
            out.append((key, f"area {mine}: {same[0]} runs"))
        elif free <= 0:
            out.append((key, NO_SLOT))
        else:
            out.append((key, None))
            taken.append(issue)
            free -= 1
    return out


def _seconds(stamp: object) -> float | None:
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def waiting_since(pr: Mapping[str, Any], why: str, cfg: Config) -> float | None:
    """When the pull request began to wait for a fix (seconds since the epoch): the review that asked for changes, the newest
    run of a check that failed, or the branch update's comment. None when GitHub gives no time: then nothing escalates.
    A request for changes with a commit newer than the review is a fix that a session pushed: it waits for the next
    round, not for a fix, so it gives None too (after the ready mark the old review stays the latest until that
    round ends; rounds 2 and 3 alike)."""
    if why == CHANGES_REQUESTED:
        review = latest_review(pr)
        asked = _seconds(review.get("submittedAt")) if review else None
        pushed = [t for c in pr.get("commits") or [] if (t := _seconds(c.get("committedDate"))) is not None]
        return None if asked is not None and any(t > asked for t in pushed) else asked
    if why == FAILED:
        # The newest of the failed checks and the latest verdict: a review that asks for changes after the gates failed
        # sends the issue to In Progress, and the session reads it within a minute
        names = {*cfg.required_checks, cfg.verdict_check}
        times = [_seconds(c.get("completedAt") or c.get("startedAt")) for c in newest_checks(pr.get("statusCheckRollup") or [])
                 if _check_name(c) in names and _check_bad(c)]
        review = latest_review(pr)
        times.append(_seconds(review.get("submittedAt")) if review else None)
        return max((t for t in times if t is not None), default=None)
    marked = [_seconds(c.get("createdAt")) for c in pr.get("comments") or [] if is_conflict_comment(c, cfg)]
    return max((t for t in marked if t is not None), default=None)


GATES_RERUN_WINDOW = 25 * 60  # seconds: the review watchdog reruns a `gates` run that failed with no job within 25 minutes of its end


def rerun_pending(pr: Mapping[str, Any], why: str, since: float, now: float, cfg: Config) -> bool:
    """A rerun of the gates can still come for a pull request whose wait is `failed` (the owner's call, 2026-11-18:
    the round does nothing until it ends; agents/implementer.md "When a review asks for changes"). Two cases, from the
    pull request's own checks: a required check runs and none failed (a newer attempt beside the old attempt's failed `verdict`,
    which stays until the rerun ends), no required check exists on the head (the `gates` run failed with no job) and the wait is
    younger than `GATES_RERUN_WINDOW`, so the watchdog's rerun can come. After that the normal ladder starts (since OWL-30 the `verdict` never
    fails for the gates, so a passing gates rerun leaves no gap to wait for)."""
    if why != FAILED:
        return False
    required = [c for c in newest_checks(pr.get("statusCheckRollup") or []) if _check_name(c) in cfg.required_checks]
    # GitHub's rollup keeps one entry for each check name, the newest attempt: a rerun shows as a running required check beside
    # the old attempt's failed `verdict`, and the old failed job is gone. A bad required check is a real failure.
    if any(_check_bad(c) for c in required):
        return False
    if any(str(c.get("status") or "COMPLETED").upper() != "COMPLETED" for c in required):
        return True
    if not required:
        return now - since < GATES_RERUN_WINDOW
    return False


ESCALATION = "This issue waits for a fix"  # the first words of the orchestrator's escalation comment


def escalated_since(issue: Mapping[str, Any], since: float) -> bool:
    """The issue has the orchestrator's escalation comment for this wait already: one that starts with ESCALATION and is
    not older than the event. A later event (a new review, new failed gates) gets a comment of its own."""
    for comment in issue.get("comments") or []:
        made = _seconds(comment.get("created"))
        if str(comment.get("body") or "").startswith(ESCALATION) and made is not None and made >= since:
            return True
    return False


def fix_run_line(model: str, effort: str, review: tuple[str, str], number: int, profile: str = "", workers: int = 0) -> str:
    """The run line of a session that the orchestrator starts for a fix: the run line, then the words `fix round`.
    The skill `measure` reads the model and the effort level before the bracket."""
    return f"{run_line(model, effort, review, profile=profile, workers=workers)} (fix round of PR #{number})"


def fix_started_at(issue: Mapping[str, Any]) -> float | None:
    """When the orchestrator last started a session for the issue's fix, read from the issue's comments, so a restart loses
    nothing: the newest comment that is a run line with the words `fix round`, unless an escalation comment came after it (the
    escalation ends that start's cycle: the owner's answer starts the count again). None when there is none."""
    comments = [(made, str(c.get("body") or "")) for c in issue.get("comments") or [] if (made := _seconds(c.get("created"))) is not None]
    starts = [made for made, body in comments if body.startswith("Run: ") and "(fix round" in body.split("\n")[0]]
    last = max(starts, default=None)
    if last is None or any(made > last and body.startswith(ESCALATION) for made, body in comments):
        return None
    return last


def run_settings(labels: Sequence[str], cfg: Config) -> tuple[str, str, str]:
    """The model, the effort level and the profile of a run, from the issue's difficulty labels as they are now:
    one cell of the inference profile in force (`run_cells`, from its `[profiles.NAME]` table) per pair of
    `needs-judgment` and `intricate`. An issue with neither label gets the first cell. The profile is the one that the cell
    names, else the profile in force: its harness and provider run the session (`cell_config`)."""
    have = set(labels)
    index = 2 * (cfg.judgment_label in have) + (cfg.intricate_label in have)
    model, effort = cfg.run_cells[index]
    return model, effort, cfg.cell_profiles[index] if cfg.cell_profiles else cfg.inference_profile


def cell_config(cfg: Config, profile: str, effort: str) -> Config:
    """The config that starts a cell on `profile`: the profile in force as it is, or a copy with the other
    profile's harness, command, permission mode and credential. The local review stays the profile in force's (the owner's
    call, 2026-11-23: one review model for the whole mix): `format_run` gives the session `--review-profile`. ConfigError when the session host has no command for that
    profile's harness: the preflight refuses such a mix first (`check_mix`)."""
    if profile == cfg.inference_profile:
        return cfg
    other = dict(cfg.cell_runs)[profile]
    return replace(cfg, inference_profile=profile, profile_key_check=other.key_check, permission_mode=other.permission,
                   credential=other.credential, harness=other.harness[0], skill_prompt=other.harness[1],
                   stop_patterns=other.harness[2], cell_profiles=(), cell_runs=(),
                   run_command=run_template(cfg.session_host, other.harness[0], Path(f"[profiles.{profile}]"), effort=bool(effort)))


def run_line(model: str, effort: str, review: tuple[str, str] = ("", ""), token: str = "", profile: str = "",
             workers: int = 0) -> str:
    """The run line of agents/implementer.md "The run line", which the skill `measure` reads. The local review's
    model and effort level come after `review`: a review on another model than the run's finds other faults, and
    `measure` groups the reviews by the model that made them. Then the profile that ran the cell: `; profile
    NAME`. A start from Todo adds its start token in a bracket: `Run: MODEL, effort EFFORT; review MODEL, effort
    EFFORT; profile NAME (start TOKEN)`. A profile whose harness takes no effort level gives the run none:
    `effort none`. A start that gave the session a share of pytest workers adds `; pytest workers N` after the profile."""
    line = f"Run: {model}, effort {effort or 'none'}"
    if review[0]:
        line += f"; review {review[0]}, effort {review[1] or 'none'}"
    if profile:
        line += f"; profile {profile}"
    if workers:
        line += f"; pytest workers {workers}"
    return f"{line} (start {token})" if token else line


def start_token(at: float, host_name: str) -> str:
    """The token of one start: its UTC time to the second and the host's name, so two starts of one key never
    share it. The orchestrator writes it in its run line and gives it to the session in its start prompt (`{token}`)."""
    name = re.sub(r"[^A-Za-z0-9.-]+", "-", host_name).strip(".-")[:63] or "host"
    return f"{datetime.fromtimestamp(at, UTC):%Y%m%dT%H%M%SZ}-{name}"


RUN_TOKEN = re.compile(r"^Run: [^\n]*\(start ([0-9]{8}T[0-9]{6}Z-[A-Za-z0-9.-]{1,63})\)")


def pending_start(issue: Mapping[str, Any], now: float, window: int) -> tuple[str, float] | None:
    """(token, time) of the issue's start that no session has claimed yet: its newest run line carries a start
    token and is younger than `window` seconds. None when the newest run line has no token (a session writes its own run
    line after its claim, a fix round's line has none, and so does a line from before the start tokens), is older, or there is none."""
    runs = [(made, body) for c in issue.get("comments") or []
            if (body := str(c.get("body") or "")).startswith("Run: ") and (made := _seconds(c.get("created"))) is not None]
    if not runs:
        return None
    made, body = max(runs, key=lambda run: run[0])
    found = RUN_TOKEN.match(body)
    return (found.group(1), made) if found and now - made < window else None


# `{env}` is ` --env VAR=` for the key variable of each other profile of the catalog: the daemon holds the keys
# of every profile that the cells use (and may hold more), and an agent's empty variable hides all but its own (`hidden_keys`)
# `--label profile={profile}` names the cell's profile for the stop reading: a stop pauses only the cells of its own profile
# `{workers_env}` and `{workers_flag}` carry the run's share of pytest workers (`pytest_workers`): Paseo sets the variable of the agent, and
# the other hosts start `agent_user.py session`, which sets it from `--workers N`
PASEO_START = 'paseo run --no-wait --quiet --cwd {cwd} --title {label} --label orchestrator={key} --label profile={profile}{env}{workers_env}'
CLAUDE_OPTIONS = '--permission-mode {permission} --model {model} --effort {effort} {prompt}'
RUN_TEMPLATES = {
    ("herdr", "claude"): 'claude --profile {profile}{review_flag}{workers_flag} ' + CLAUDE_OPTIONS,
    # herdr types the command's first word as the agent's executable (`agent start --kind pi`): the pane's `pi` alias goes to
    # `agent_user.py pi`, which builds the session's environment
    ("herdr", "pi"): 'pi --profile {profile}{review_flag}{workers_flag} --model {model} --thinking {effort} {prompt}',
    ("paseo", "claude"): PASEO_START + ' --provider claude --model {model} --thinking {effort} --mode {permission} {prompt}',
    ("paseo", "pi"): PASEO_START + ' --provider pi --model {model} --thinking {effort} {prompt}',
    ("process", "claude"): ('/usr/bin/python3 -I {wrapper} session --profile {profile}{review_flag}{workers_flag} --keep-cwd --print '
                            '--output-format stream-json --verbose ' + CLAUDE_OPTIONS),
}


def run_template(host: str, harness: str, path: Path, *, effort: bool) -> str:
    if template := RUN_TEMPLATES.get((host, harness)):
        flag = "--thinking" if harness == "pi" or host == "paseo" else "--effort"
        return template if effort else template.replace(f" {flag} {{effort}}", "")
    pairs = ", ".join(f"{h}/{agent}" for h, agent in RUN_TEMPLATES)
    raise ConfigError(f"{path}: unsupported session host/harness pair {host}/{harness}; supported pairs: {pairs}")


PLACES = re.compile(r"\{(key|mode|model|effort|label|cwd|token|profile|review_flag|permission|prompt|wrapper|env|workers_env|workers_flag)\}")
ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]{0,63}")  # a variable that `{env}` empties: it goes into a shell command


def places(run_command: str, skill_prompt: str) -> set[str]:
    """The places that a run's command holds: those of `run_command`, and with `{prompt}` those of the harness's
    `skill_prompt` too."""
    found = set(PLACES.findall(run_command))
    return found | set(PLACES.findall(skill_prompt)) if "prompt" in found else found


def uses(cfg: Config, place: str) -> bool:
    """The run's command holds `{place}`, in `run_command` or in the harness's `skill_prompt` that `{prompt}` puts there."""
    return place in places(cfg.run_command, cfg.skill_prompt)


def agent_wrapper() -> Path:
    """Use the wrapper from this install, or the checkout when run by hand."""
    folder = Path(__file__).resolve().parent
    wrapper = folder / "agent_user.py"
    return wrapper if wrapper.is_file() else folder.parent / ".workflow" / wrapper.name


def command_for(template: str, key: str, mode: str, model: str = "", effort: str = "", label: str = "", cwd: str = "",
                token: str = "", profile: str = "", permission: str = "", prompt: str = "", review_profile: str = "",
                hide: Sequence[str] = (), workers: int = 0) -> str:
    """The run's command: the template with `{key}`, `{mode}`, `{model}`, `{effort}`, `{label}`, `{cwd}`, `{token}`,
    `{profile}`, `{permission}` and `{prompt}` filled in, in one pass, so a value never fills a place of another. Each goes into a shell: its shape is checked, and the label (it holds a space) and the folder are quoted.
    `{prompt}` is the harness's `skill_prompt` with its `{key}` and `{token}` filled in, quoted as one word.
    A host that starts the agent itself (Paseo) takes the label and the folder in its command. `{token}` is the
    start token (`start_token`), which the start prompt gives to the session. `{profile}` is the inference
    profile's name, which `agent_user.py session --profile NAME` turns into the session's provider and key.
    `{permission}` is the profile's permission mode, the value of `--permission-mode` (claude) or `--mode` (Paseo).
    `{review_flag}` is empty, or ` --review-profile NAME` when `review_profile` (the profile in force) is not `profile` (the
    cell's): the session then runs its local review on the profile in force's model.
    `{env}` is ` --env NAME=` for each name of `hide`: an empty variable, which Claude Code and pi both read as a
    missing key (measured 2026-11-23, Paseo 0.17.1). It holds no secret.
    `workers` is the run's share of pytest workers (`pytest_workers`); 0 gives none. `{workers_env}` is ` --env
    PYTEST_XDIST_AUTO_NUM_WORKERS=N` (Paseo) and `{workers_flag}` is ` --workers N` (`agent_user.py session`)."""
    if not KEY_SHAPE.fullmatch(key) or mode not in MODES:
        raise ValueError(f"refused to start {key!r} in mode {mode!r}")
    whole = places(template, prompt)
    if "prompt" in whole and (not prompt or set(PLACES.findall(prompt)) - set(PROMPT_PLACES)):
        raise ValueError(f"refused to start {key!r} with the prompt {prompt!r}")
    if "token" in whole and not TOKEN_SHAPE.fullmatch(token):
        raise ValueError(f"refused to start {key!r} with the token {token!r}")
    for name, value in (("model", model), ("effort", effort), ("profile", profile)):
        if name in whole and not SETTING_SHAPE.fullmatch(value):
            raise ValueError(f"refused to start {key!r} with the {name} {value!r}")
    if "review_flag" in whole and review_profile and not SETTING_SHAPE.fullmatch(review_profile):
        raise ValueError(f"refused to start {key!r} with the review profile {review_profile!r}")
    if "{permission}" in template and permission not in PERMISSION_MODES:
        raise ValueError(f"refused to start {key!r} with the permission mode {permission!r}")
    if not isinstance(workers, int) or workers < 0:
        raise ValueError(f"refused to start {key!r} with {workers!r} workers")
    if bad := next((name for name in hide if not ENV_NAME.fullmatch(name)), None):
        raise ValueError(f"refused to start {key!r} with the variable {bad!r}")
    if "{label}" in template and not SESSION_LABEL.fullmatch(label):
        raise ValueError(f"refused to start {key!r} with the label {label!r}")
    if "{cwd}" in template and not cwd:
        raise ValueError(f"refused to start {key!r} with no folder")
    values = {"key": key, "mode": mode, "model": model, "effort": effort, "label": shlex.quote(label), "cwd": shlex.quote(cwd),
              "token": token, "profile": profile, "permission": permission,
              "review_flag": f" --review-profile {review_profile}" if review_profile and review_profile != profile else "",
              "env": "".join(f" --env {name}=" for name in hide),
              "workers_env": f" --env {WORKERS_VARIABLE}={workers}" if workers else "",
              "workers_flag": f" --workers {workers}" if workers else ""}
    if "wrapper" in whole:
        values["wrapper"] = shlex.quote(str(agent_wrapper()))
    values["prompt"] = shlex.quote(PLACES.sub(lambda m: values[m.group(1)], prompt))
    return PLACES.sub(lambda m: values[m.group(1)], template)


# The workspaces that the orchestrator labels (`session_label`: `KEY ·` for a key's first start, `KEY · #N` for the Nth: the
# key as Linear shows it, then the orchestrator's mark). A workspace that the owner opened has another label, and
# nothing here closes it. The label holds no time: a run's age comes from its run line (`run_started`).
# The older label (`abc-668-start-20261110T151200`) is read too: a workspace made before an install still closes.
SESSION_LABEL = re.compile(r"^([A-Z][A-Z0-9]{0,9}-[0-9]{1,7}) ·(?: #[0-9]{1,3})?$|^([a-z][a-z0-9]{0,9}-[0-9]{1,7})-start-[0-9]{8}T[0-9]{6}$")
BUSY = {"working", "blocked"}  # herdr's agent states of a session that has not finished
FINISHED = {"idle", "done"}  # herdr's agent states of a session that may close: "unknown" stays open too
HOUR = 3600


def key_variable(profile: str) -> str:
    """The variable that holds the profile's key in its harness (`key_variable` of the catalog's connection), or "" for a
    profile that signs itself in. A profile that the catalog does not know has none."""
    catalog = _model_catalog()
    route = catalog["profiles"].get(profile)
    if not route:
        return ""
    return str(catalog["providers"][route["provider"]]["connections"][route["harness"]].get("key_variable") or "")


def daemon_profiles(cfg: Config) -> tuple[str, ...]:
    """The profiles that Paseo's daemon holds the keys of: the profile in force, which gives the daemon its local
    review, then each other profile that a cell names, in the order of CELLS. `agent_user.py paseo-daemon` reads
    the same set from the `cells` of NAME in `local-review.json`."""
    return (cfg.inference_profile, *(name for name, _ in cell_groups(cfg)))


def hidden_keys(cfg: Config, profile: str) -> tuple[str, ...]:
    """The key variables that an agent on `profile` empties: the daemon's environment holds the key of every
    profile that it started with, each agent inherits it, and `paseo run --env VAR=` gives the agent an empty variable, so the
    agent keeps only its own profile's key. The daemon may hold more profiles than the cells name now (a cell dropped since it
    started: the preflight passes such a daemon), so the key of every profile of the catalog is emptied, not only those of
    `daemon_profiles`. A variable that two profiles share stays (one value: the daemon refuses two)."""
    own = key_variable(profile)
    return tuple(sorted({var for name in _model_catalog()["profiles"] if (var := key_variable(name)) and var != own}))


WORKERS_VARIABLE = "PYTEST_XDIST_AUTO_NUM_WORKERS"  # the number of workers that `pytest -n auto` starts (pytest-xdist)


def pytest_workers(max_runs: int, cpus: int | None = None) -> int:
    """One run's share of the CPUs of its session endpoint's machine for `pytest -n auto`: that machine's CPU count (the
    endpoint's `cpus`; with none, the orchestrator's machine's) divided by `max_runs`, and at least 2. `max_runs` is one cap
    for all products, so the products of one endpoint can hold every slot of it: the products on one machine share its CPUs,
    and a product on another machine uses that machine's count. A count of the slots that the endpoint holds now would give
    the first run all CPUs, and the runs that start after it would overload the machine. Without the share each run starts one
    worker per CPU, and `max_runs` runs overload the Mac (OWL-34: a load average of 21 on 14 cores)."""
    return max(2, (cpus or os.cpu_count() or 1) // max(1, max_runs))


def format_run(cfg: Config, key: str, mode: str, model: str, effort: str, label: str, cwd: str, token: str,
               profile: str = "") -> str:
    """The run's command on the cell's profile (`cell_config`), the profile in force when `profile` is empty. On
    Paseo each agent also empties the key variables of every other profile (`hidden_keys`)."""
    in_force = cfg.inference_profile
    hide = hidden_keys(cfg, profile or in_force) if cfg.session_host == "paseo" else ()
    cfg = cell_config(cfg, profile or in_force, effort)
    return command_for(cfg.run_command, key, mode, model, effort, label, cwd, token, cfg.inference_profile,
                       cfg.permission_mode, cfg.skill_prompt, in_force, hide, pytest_workers(cfg.max_runs, cfg.endpoint.cpus))


def session_label(key: str, start: int = 1) -> str:
    """The label of a key's `start`th start in this process: `ABC-668 ·`, then `ABC-668 · #2`."""
    return f"{key.upper()} ·" if start <= 1 else f"{key.upper()} · #{start}"


def own_key(label: object) -> str | None:
    """The issue key of a workspace that the orchestrator labelled, else None."""
    found = SESSION_LABEL.match(str(label or ""))
    return (found.group(1) or found.group(2)).upper() if found else None


def agent_profile(agent: Mapping[str, Any]) -> str | None:
    """The profile in an agent's label `profile=NAME`, None when it has none (or one of a wrong shape)."""
    labels = agent.get("labels")
    name = labels.get("profile") if isinstance(labels, dict) else None
    return name if isinstance(name, str) and SETTING_SHAPE.fullmatch(name) else None


def session_number(label: object) -> int:
    """The number of a session's start in its label: `KEY ·` is 1, `KEY · #3` is 3, so the newest session of a key has the largest."""
    found = re.search(r" #([0-9]{1,3})$", str(label or ""))
    return int(found.group(1)) if found else 1


# Paseo's `created` in `paseo ls -g --json` is a relative text on CLI 0.16.1 ("1 minutes ago", "3 days ago"), so
# its order as text is wrong ("9 hours ago" > "2 minutes ago"). `created_age` reads it as seconds; an ISO time is read too.
RELATIVE_AGE = re.compile(r"(\d+|an?) (second|minute|hour|day|week|month|year)s? ago")
AGE_UNITS = {"second": 1, "minute": 60, "hour": HOUR, "day": 24 * HOUR, "week": 7 * 24 * HOUR, "month": 30 * 24 * HOUR,
             "year": 365 * 24 * HOUR}


def created_age(value: object, now: float | None = None) -> float:
    """A session's age in seconds from Paseo's `created`: a relative text or an ISO time. Unknown text is the oldest (inf)."""
    text = " ".join(str(value or "").lower().split())
    if found := RELATIVE_AGE.fullmatch(text):
        count = 1 if found.group(1) in ("a", "an") else int(found.group(1))
        return float(count * AGE_UNITS[found.group(2)])
    if text in ("now", "just now", "a few seconds ago"):
        return 0.0
    made = _seconds(str(value).strip()) if text else None
    return math.inf if made is None else max(0.0, (time.time() if now is None else now) - made)


TODO_WAKE_TEXT = "The issue is in Todo again. Read its newest comments, then go on from your branch."

TODO_WOKEN_WAIT = "woken for Todo, its idle session is on it: it holds a slot and its Area"  # the plan's line for a woken pick
RETRY_TEXT = "Retry"  # the wake that gives a turn stopped on an API error one more try


def wake_text(number: int, why: str) -> str:
    """The message that wakes the session which opened pull request `number`: one line, built from the number and
    the cause only (`why` is one of `can_resume`'s fixed words), never an issue's text. A host sends it as a file or as typed text."""
    return (f"PR #{number} waits for a fix ({why}). Run the fix round now, as agents/implementer.md \"When a review asks for "
            f"changes\" says: first convert the pull request to draft (`gh pr ready {number} --undo`), then write the run line.")


@dataclass(frozen=True)
class Closing:
    """A workspace to close: its id, its issue's key, the issue's final state and the whole hours since it entered it."""

    workspace: str
    key: str
    state: str
    hours: int


def space_key(space: Mapping[str, Any]) -> str | None:
    """The issue key of a workspace entry: its `key` (a Paseo workspace with no agent left), else its label's."""
    return str(space["key"]) if space.get("key") else own_key(space.get("label"))


def to_close(workspaces: Sequence[Mapping[str, Any]], issues: Mapping[str, Mapping[str, Any]], now: float,
             close_after_hours: int, final_states: Sequence[str]) -> list[Closing]:
    """The workspaces to close. Each one passes every test: its label is the orchestrator's (`own_key`), herdr shows
    its agent idle or done, its issue (`issues`: key -> {"state", "since"}, `since` in seconds since the epoch or None) is in
    a final state (`final_states`: the config's `closed_states`), and it entered that state at least `close_after_hours`
    ago (the owner's call, 2026-11-10: 12, so the night's runs are still there in the morning). An issue that is missing,
    or whose time is unknown, keeps its workspace. 0 hours turns the closing off."""
    if close_after_hours <= 0:
        return []
    out: list[Closing] = []
    for space in workspaces:
        key = space_key(space)
        if key is None or str(space.get("agent_status") or "") not in FINISHED or not space.get("workspace_id"):
            continue
        found = issues.get(key) or {}
        state, since = str(found.get("state") or ""), found.get("since")
        if state not in final_states or not isinstance(since, int | float) or now - since < close_after_hours * HOUR:
            continue
        out.append(Closing(str(space["workspace_id"]), key, state, int((now - since) // HOUR)))
    return out


@dataclass(frozen=True)
class Dropped:
    """An open pull request to close: its number, its issue's key and the whole minutes since the issue was canceled.
    `label`: the skip label the issue carries, when it has one; then the pull request is the owner's to close."""

    number: int
    key: str
    minutes: int
    label: str | None = None


def team_key(branch: object, team: str) -> str | None:
    """The key of an issue of `team` in a branch name, else None: `bump-node-20` gives no key (R1-1)."""
    key = key_in(str(branch or ""))
    return key if key is not None and key.startswith(f"{team.upper()}-") else None


def to_drop(prs: Sequence[Mapping[str, Any]], issues: Mapping[str, Mapping[str, Any]], now: float, canceled: str,
            grace_minutes: int, team: str, skip_labels: Sequence[str] = ()) -> list[Dropped]:
    """The open pull requests whose issue was canceled. Each one passes every test: it is open (a merged or closed
    one is never touched), its branch carries a key of `team` (`team_key`), the issue (`issues`: key -> {"state", "since"}) is in
    the state `canceled`, and it entered that state at least `grace_minutes` ago (the owner may undo a cancel). An issue that
    is missing, or whose time is unknown, keeps its pull request open. An issue with a label of `skip_labels` (`hands-off`:
    the owner's) gives a `Dropped` with that `label`: the owner closes it by hand (the owner's call, 2026-11-11)."""
    out: list[Dropped] = []
    for pr in prs:
        key = team_key(pr.get("headRefName"), team)
        if key is None or str(pr.get("state") or "OPEN").upper() != "OPEN":
            continue
        found = issues.get(key) or {}
        since = found.get("since")
        if found.get("state") != canceled or not isinstance(since, int | float) or now - since < grace_minutes * 60:
            continue
        label = next((x for x in skip_labels if x in (found.get("labels") or ())), None)
        out.append(Dropped(int(pr["number"]), key, int((now - since) // 60), label))
    return out


def drop_comment(key: str) -> str:
    """The one comment on a pull request that the orchestrator closes."""
    return f"Closed: {key} was canceled"


# -- The round's plan: what the terminal, the log and the state file show. A GUI reads the state file, never the text.

PLAN_VERSION = 7  # 2: the skipped issues are `session` and `hands_off`, no longer lines of `other_waits`; 3: `prs`, and
# `yours` is a list of what waits on the owner, no longer a count; 4: no key `session`; 5: `pause`; 6: `pause` is a list, a row for each profile with a stop
# 7: the state file's `hosts`, each session's host (`Hosts.record`)
AREA_WAIT = "area "  # the first word of `pick_starts`' reason for the Area rule: such an issue waits in its Area's queue
NO_SLOT = "no free slot"  # an issue with an Area that waits for a slot waits in its Area's queue too: the round's line has the count
STARTS = ("started", "would_start")
QUIET = ("would_start", "would_start_fix", "would_escalate", "would_wake")  # a dry run's events that repeat each round: the loop prints them when they change
HEARTBEAT_SECONDS = 900  # a quiet loop prints one `no change since` line after this long
# The time limits of a stale pull request. Constants, not config keys: an installed config needs no new key.
NO_REVIEW_SECONDS = 20 * 60  # ready, and no `verdict` run on its head this long after its last commit: no review started
REVIEW_RUN_SECONDS = 60 * 60  # a `verdict` run in progress this long: the review run never ended
MERGE_WAIT_SECONDS = 30 * 60  # approved, every check done and green this long, and still open: auto-merge is off
DRAFT_IDLE_SECONDS = 30 * 60  # a draft with no busy session of its key and no commit this long: the session that held it ended
NO_PUSH_SECONDS = 24 * HOUR  # no commit this long and no busy session of its key: .github/triage.py STALE_PUSH, with the session
PR_BUILDING, PR_REVIEW, PR_FIX = "building", "in review", "needs a fix"
PR_APPROVED, PR_OWNER, PR_STALE = "approved, waiting for the merge", "waits on the owner", "stale"
WORKFLOW_MERGE = "Ready to merge. It changes a workflow file"  # the review workflow's comment: only the owner merges it
NEEDS_DECISION = "Needs decision:"  # the start of the body of the reviewer's needs-decision review (agents/reviewer.md)
# Why a pull request waits on the owner, as the skill `queue` reads it: the newest comment of the issue or the pull request,
# or review of the pull request, that one pattern matches gives the reason. The first pattern that matches wins (a refused
# auto-merge also starts with "The reviewer approved PR #N").
OWNER_REASONS = (
    (re.compile("^" + re.escape(NEEDS_DECISION)), "the reviewer's question: answer on the issue"),
    (re.compile(r"GitHub refused to turn on auto-merge"), "auto-merge refused: rerun the job or merge"),
    (re.compile(r"^The reviewer approved PR #\d+\. It changes files that only the owner merges"), "code owner's file: owner-approved or merge"),
    (re.compile(r"^The gates passed on a rerun at .* CODEOWNERS"), "code owner's file, green on a rerun: merge"),
    (re.compile("^" + re.escape(WORKFLOW_MERGE)), "workflow change: merge it"),
    (re.compile(r"^The gates failed on this approved head"), "failed gates on an approved head: rerun"),
    (re.compile(r"Round \d+ is not reviewed"), "blocked after the last round"),
    (re.compile("^" + re.escape(ESCALATION)), "no session took the fix"),
)


@dataclass(frozen=True)
class Event:
    """Something the round did: kind is retrying, paused, resumed, started, would_start, woke, would_wake, wake_failed, started_fix, would_start_fix, start_failed, run_line_failed, run_line_written, escalated, would_escalate,
    escalation_failed, closed, would_close, close_failed, closed_pr, would_close_pr or close_pr_failed. A start carries the
    run's model and effort level.
    `reason` goes to the state file and the JSON line: a cause or an error's type, never an error's text."""

    kind: str
    key: str
    text: str  # the line for the terminal and the log
    reason: str = ""
    model: str = ""
    effort: str = ""


@dataclass(frozen=True)
class Wait:
    """An issue in Todo that can start and does not start this round. `detail` adds an error's text to the line only."""

    key: str
    reason: str
    detail: str = ""

    @property
    def text(self) -> str:
        return f"{self.key} waits: {self.reason}{self.detail}"


def _queue_order(items: Sequence[Mapping[str, Any]], ahead: set[str], cfg: Config) -> list[Mapping[str, Any]]:
    """The issues in the order of `_order`, each blocked one after its blockers that are in the queue (or hold the Area)."""
    pending, done = sorted(items, key=_order), set(ahead)
    inside = done | {str(i["identifier"]) for i in items}
    out: list[Mapping[str, Any]] = []
    while pending:
        ready = next((i for i in pending if all(str(b.get("key")) in done or str(b.get("key")) not in inside
                                               for b in open_blockers(i, cfg))), pending[0])  # pending[0]: a cycle
        pending.remove(ready)
        out.append(ready)
        done.add(str(ready["identifier"]))
    return out


def _holder_kind(issue: Mapping[str, Any], cfg: Config) -> str:
    """How the view marks an issue that holds an Area: building or review. An Escalated issue holds no Area."""
    return "review" if issue.get("state") in cfg.review_states else "building"


def _stamp(seconds: float | None) -> str | None:
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if seconds is not None else None


def last_commit(pr: Mapping[str, Any]) -> float | None:
    return max((t for c in pr.get("commits") or [] if (t := _seconds(c.get("committedDate"))) is not None), default=None)


def owner_wait(issue: Mapping[str, Any], pr: Mapping[str, Any] | None, cfg: Config) -> tuple[str, float | None] | None:
    """Why the issue and its pull request wait on the owner, and since when, or None when they do not: the issue is in
    Escalated, the pull request carries the label `escalated`, or the review workflow's comment `WORKFLOW_MERGE` is newer
    than its last commit. The reason comes from the newest comment or review that `OWNER_REASONS` knows and that is newer
    than the pull request's last commit: each of them comes after the head it is about, and a fix always pushes a commit,
    so an older one is from a wait that ended. With none: an issue with no pull request holds a question, and else the
    owner reads both.

    A `WORKFLOW_MERGE` comment does not hold a pull request whose branch conflicts with main after its last commit: that
    conflict sends the pull request back to its session, whatever the comment says. The issue's own Escalated
    state and the label `escalated` still hold it, since the owner's action is what those wait for."""
    pr = pr or {}
    labels = {str(x.get("name") if isinstance(x, Mapping) else x) for x in pr.get("labels") or []}
    said = [(t, str(c.get("body") or "")) for c in issue.get("comments") or [] if (t := _seconds(c.get("created"))) is not None]
    said += [(t, str(c.get("body") or "")) for c in pr.get("comments") or [] if (t := _seconds(c.get("createdAt"))) is not None]
    said += [(t, str(r.get("body") or "")) for r in pr.get("reviews") or [] if (t := _seconds(r.get("submittedAt"))) is not None]
    commit = last_commit(pr)
    said = [(t, body) for t, body in said if commit is None or t > commit]
    merge = any(body.startswith(WORKFLOW_MERGE) for _, body in said) and not conflict_after_last_commit(pr, cfg)
    if issue.get("state") != cfg.escalated and cfg.escalated_label not in labels and not merge:
        return None
    for t, body in sorted(said, key=lambda s: -s[0]):
        if reason := next((why for pattern, why in OWNER_REASONS if pattern.search(body)), None):
            return reason, t
    return ("a question on the issue" if not pr else "not clear: read the issue"), None


def pr_status(issue: Mapping[str, Any], pr: Mapping[str, Any], cfg: Config, now: float,
              busy: Collection[str] | None) -> tuple[str, str, float | None]:
    """One state of an open pull request, from the artifacts only: (state, reason, since). `busy`: the keys with a
    busy session (None: the host's list could not be read, so nothing is stale for want of a session). The state is
    `waits on the owner` (`owner_wait`), `building` (a draft), `needs a fix` (`fix_reason`), `approved, waiting for the merge`,
    `in review`, or `stale` with the limit that it passed. The plan only shows a stale pull request: nothing acts on it."""
    if owner := owner_wait(issue, pr, cfg):
        return PR_OWNER, owner[0], owner[1]
    commit = last_commit(pr)
    idle = busy is not None and str(issue["identifier"]) not in busy
    if pr.get("isDraft"):
        if idle and commit is not None and now - commit >= DRAFT_IDLE_SECONDS:
            return PR_STALE, f"draft, no session busy, no commit in {DRAFT_IDLE_SECONDS // 60} min", commit
        return PR_BUILDING, "", commit
    checks = newest_checks(pr.get("statusCheckRollup") or [])
    verdict = next((c for c in checks if _check_name(c) == cfg.verdict_check), None)
    state, why, since = PR_REVIEW, fix_reason(pr, cfg) or "", commit
    if why == CHANGES_REQUESTED and waiting_since(pr, why, cfg) is None:
        why = ""  # a fix is pushed after the request: it waits for its next review, not for a fix (`waiting_since`)
    if why:
        state, since = PR_FIX, waiting_since(pr, why, cfg)
    elif pr_state(pr, cfg) == APPROVED:
        names = {*cfg.required_checks, cfg.verdict_check}  # a check that has not started yet is not done
        done = [c for c in checks if _check_name(c) in names and str(c.get("status") or "COMPLETED").upper() == "COMPLETED"]
        ends = [_seconds(c.get("completedAt")) for c in done]
        green = {_check_name(c) for c in done} == names and None not in ends
        end = max((t for t in ends if t is not None), default=None)
        if green and end is not None and now - end >= MERGE_WAIT_SECONDS:
            return PR_STALE, f"approved and green, not merged in {MERGE_WAIT_SECONDS // 60} min", end
        return PR_APPROVED, "", end
    elif verdict is not None and str(verdict.get("status") or "COMPLETED").upper() != "COMPLETED":
        since = _seconds(verdict.get("startedAt"))
        if since is not None and now - since >= REVIEW_RUN_SECONDS:
            return PR_STALE, f"review run not ended in {REVIEW_RUN_SECONDS // 60} min", since
    elif verdict is None and commit is not None and now - commit >= NO_REVIEW_SECONDS:
        return PR_STALE, f"no review run {NO_REVIEW_SECONDS // 60} min after the last commit", commit
    if idle and commit is not None and now - commit >= NO_PUSH_SECONDS:
        return PR_STALE, f"no commit in {NO_PUSH_SECONDS // HOUR} h, no session busy", commit
    return state, why, since


def build_plan(issues: Sequence[Mapping[str, Any]], prs: Sequence[Mapping[str, Any]], cfg: Config, *, stamp: str,
               slots: Mapping[str, str], running: Sequence[str], areas: Mapping[str, str | None], events: Sequence[Event],
               waits: Sequence[Wait], stacks: Sequence[tuple[str, str]], by_hand: Sequence[Dropped] = (),
               busy: Collection[str] | None = None, pause: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """The round as data: the counts, `pause` (a row for each profile with a stop: its retry's wait or its pause, with the key, cause and phase), one queue per Area (the issues that hold it, then its issues in Todo in the order they
    start: those that wait for the Area or for a slot, and the blocked ones), the slots in use with why each counts
    (`Runner.held`), the other waits, the count of the issues in Todo that a skip label holds back (`hands_off`), the events, the draft stacks, and the open pull requests of canceled issues
    that the owner closes by hand (`close_by_hand`). Each open pull request of an issue with its state (`prs`,
    `pr_status`; `busy` as it takes it), and what waits on the owner (`yours`: each key, its pull request and the reason). Keys, Area names, states and reasons only: no issue text."""
    by_key = {str(i["identifier"]): i for i in issues}
    now = _seconds(stamp) or 0.0
    states: list[dict[str, Any]] = []
    yours: list[dict[str, Any]] = []
    for i in sorted(issues, key=_order):
        key, found = str(i["identifier"]), pr_for(str(i["identifier"]), prs)
        if found is not None:
            state, reason, since = pr_status(i, found, cfg, now, busy)
            states.append({"key": key, "number": int(found.get("number") or 0), "state": state, "reason": reason, "since": _stamp(since)})
            if state == PR_OWNER:
                yours.append({"key": key, "pr": states[-1]["number"], "reason": reason})
        elif i.get("state") == cfg.escalated and (owner := owner_wait(i, None, cfg)):
            yours.append({"key": key, "pr": None, "reason": owner[0]})
    with_pr = open_pr_keys(issues, prs)
    started = [e.key for e in events if e.kind in STARTS]
    holders = [k for k in running if k in by_key] + [k for k in started if k not in running]
    queued = [w for w in waits if w.reason.startswith(AREA_WAIT) or (w.reason == NO_SLOT and areas.get(w.key))]
    other = [w for w in waits if w not in queued]
    in_queue = {w.key for w in queued}
    blocked = {k for k, i in by_key.items() if i.get("state") == cfg.todo and open_blockers(i, cfg)
               and not set(i.get("labels") or []) & set(cfg.skip_labels) and i.get("size") != cfg.too_big_size}
    in_queue |= blocked - {w.key for w in other} - set(holders)

    def blockers(issue: Mapping[str, Any]) -> list[dict[str, Any]]:
        return [{"key": str(b.get("key")), "state": str(b.get("state")), "area": areas.get(str(b.get("key")))}
                for b in open_blockers(issue, cfg)]

    queues: list[dict[str, Any]] = []
    for name in cfg.areas:
        held = [{"key": k, "state": "starting" if k in started or by_key[k].get("state") == cfg.todo else str(by_key[k].get("state")),
                 "kind": _holder_kind(by_key[k], cfg)} for k in holders if areas.get(k) == name]
        line = {h["key"] for h in held}
        queue: list[dict[str, Any]] = []
        for issue in _queue_order([by_key[k] for k in in_queue if areas.get(k) == name], line, cfg):
            key, mark = str(issue["identifier"]), None
            outside = [b for b in blockers(issue) if b["key"] not in line]
            if outside and (on := stack_blocker(issue, cfg, with_pr)):
                mark = f"stacks on {on}"
            elif outside:
                first = outside[0]
                mark = f"after {first['key']} ({first['area'] if first['key'] in by_key and first['area'] else first['state']})"
            line.add(key)
            queue.append({"key": key, "mark": mark, "blockers": blockers(issue)})
        if held or queue:
            queues.append({"area": name, "holders": held, "queue": queue,
                           "next": next((q["key"] for q in queue if q["mark"] is None), None)})
    reviewing = [i for i in issues if i.get("state") in cfg.review_states and i.get("state") != cfg.escalated
                 and str(i["identifier"]) in with_pr]
    hands_off = sum(1 for i in issues if i.get("state") == cfg.todo and set(i.get("labels") or []) & set(cfg.skip_labels))
    return {
        "version": PLAN_VERSION, "time": stamp, "poll_seconds": cfg.poll_seconds, "building": len(slots), "max_runs": cfg.max_runs,
        "reviewing": len(reviewing),
        "yours": yours, "prs": sorted(states, key=lambda p: p["number"]),
        "slots": [{"key": k, "reason": why} for k, why in slots.items()], "areas": queues,
        "other_waits": [{"key": w.key, "reason": w.reason} for w in other],
        "hands_off": hands_off,
        "events": [{"event": e.kind, "key": e.key, **({"reason": e.reason} if e.reason else {}),
                    **({"model": e.model, "effort": e.effort} if e.model else {})} for e in events],
        "pause": [dict(row) for row in pause],
        "stacks": [{"key": k, "on": on} for k, on in stacks],
        "close_by_hand": [{"key": d.key, "pr": d.number, "label": d.label} for d in sorted(by_hand, key=lambda d: d.number)],
    }


def shown_state(plan: Mapping[str, Any]) -> dict[str, Any]:
    """The part of a plan that the loop prints only when it changed: the counts, the slots' keys (a slot's age
    changes each minute), `pause`, a dry run's repeated events, the queues, the other waits, the hands-off count, the stacks, the closes by hand,
    `yours`, and each pull request's state and reason (not its `since`: a reason names its limit, never an age)."""
    return {"counts": counts_text(plan), "slots": [s["key"] for s in plan["slots"]], "pause": plan.get("pause"),
            "quiet": [e for e in plan["events"] if e["event"] in QUIET], **{k: plan[k] for k in ("areas", "other_waits", "stacks")},
            "hands_off": plan.get("hands_off") or 0, "close_by_hand": plan.get("close_by_hand") or [],
            "yours": plan.get("yours"), "prs": [{k: p[k] for k in ("key", "number", "state", "reason")} for p in plan.get("prs") or []]}


def by_hand_text(close: Mapping[str, Any]) -> str:
    """One pull request of a canceled issue that the owner closes by hand."""
    return f"{close['key']} canceled: close PR {close['pr']} by hand"


def hands_off_text(plan: Mapping[str, Any]) -> str:
    return f"{plan.get('hands_off') or 0} hands-off: /queue"


def your_count(plan: Mapping[str, Any]) -> int:
    """How many things wait on the owner: the length of `yours`, or the count of a plan from before `yours` was a list."""
    yours = plan.get("yours") or []
    return len(yours) if isinstance(yours, list) else int(yours)


def your_text(item: Mapping[str, Any]) -> str:
    """One thing that waits on the owner: `ABC-7 #70 code owner's file: owner-approved or merge`."""
    number = f" #{item['pr']}" if item.get("pr") else ""
    return f"{item['key']}{number} {item['reason']}"


def pr_text(item: Mapping[str, Any]) -> str:
    """One open pull request of the plan: `ABC-7 stale: no review run 20 min after the last commit`."""
    return f"{item['key']} {item['state']}" + (f": {item['reason']}" if item.get("reason") else "")


def shown_prs(plan: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The pull requests that the text and the view list: the ones that wait on the owner show in `yours` only."""
    return [p for p in plan.get("prs") or [] if p["state"] != PR_OWNER]


def hosts_text(plan: Mapping[str, Any]) -> list[str]:
    """One line for each host while two are in use: the host and the keys of its sessions, the host in force
    first with `new starts`, an old host that does not answer with `no answer`. None with one host."""
    hosts = plan.get("hosts")
    if not isinstance(hosts, dict):
        return []
    in_force, keys = str(hosts.get("in_force") or ""), dict[str, set[str]]()
    for row in hosts.get("sessions") or []:
        if isinstance(row, dict):
            keys.setdefault(str(row.get("host")), set()).add(str(row.get("key")))
    names = [in_force, *sorted(set(keys) - {in_force})]
    if len(names) < 2:
        return []
    silent = hosts.get("unreached") or {}
    return [f"{name} ({'new starts' if name == in_force else 'no answer' if name in silent else 'old host'}): "
            f"{', '.join(sorted(keys.get(name, ()))) or 'no session'}" for name in names]


def counts_text(plan: Mapping[str, Any]) -> str:
    return f"build {plan['building']}/{plan['max_runs']} · review {plan['reviewing']} · you {your_count(plan)}"


def plan_text(plan: Mapping[str, Any], events: Sequence[Event], in_progress: str = "In Progress") -> list[str]:
    """The grouped lines: the round's line (the full UTC time, then the counts), the slots in use with why each counts
    (when a slot is held), `pause`'s retrying or paused line, the events, then one line per Area
    (`docs  ABC-602 ▸ ABC-636, ABC-639`), the other waits, the draft stacks, one row
    per open pull request with its state (`#897  ABC-943 in review`; the ones that wait on the owner are in `yours` only), and
    the row `yours`: what waits on the owner with its reason, the count of the `hands-off` issues and the pull
    requests of canceled issues that the owner closes by hand."""
    lines = [f"{plan['time']} {counts_text(plan)}"]
    if plan["slots"]:
        lines.append("slots: " + " · ".join(f"{s['key']} ({s['reason']})" for s in plan["slots"]))
    pauses = plan.get("pause") or []
    for pause in [pauses] if isinstance(pauses, Mapping) else pauses:  # a state file of before plan version 6 holds one row
        name = pause.get("profile") or "no profile label"
        if pause["phase"] == "retrying":
            lines.append(f"retrying {pause['key']}{f' on {name}' if 'profile' in pause else ''} after an API error ({pause['cause']})")
        else:
            lines.append(f"paused ({name}): {pause['cause']} (the newest session, of {pause['key']}, stopped on an API error)"
                         if "profile" in pause else f"paused: {pause['cause']}")
    for kind, verb in (("started", "started"), ("would_start", "would start")):
        if keys := [f"{e.key} ({e.model}, {e.effort})" if e.model else e.key for e in events if e.kind == kind]:
            lines.append(f"{verb} {', '.join(keys)}")
    lines += [e.text for e in events if e.kind not in STARTS]
    rows: list[tuple[str, str]] = []
    for q in plan["areas"]:
        held = ", ".join(h["key"] + ("" if h["state"] == in_progress else f" ({h['state'].lower()})") for h in q["holders"])
        queue = ", ".join(i["key"] + (f" {i['mark']}" if i["mark"] else "") for i in q["queue"])
        rows.append((q["area"], " ▸ ".join(p for p in (held, queue) if p) if held else f"▸ {queue}"))
    if plan["other_waits"]:
        rows.append(("other waits", " · ".join(f"{w['key']} {w['reason']}" for w in plan["other_waits"])))
    if plan["stacks"]:
        rows.append(("draft stacks", ", ".join(f"{s['key']} on {s['on']}" for s in plan["stacks"]) + " (by hand: /implement)"))
    rows += [(f"#{p['number']}", pr_text(p)) for p in shown_prs(plan)]
    listed = [your_text(y) for y in plan["yours"]] if isinstance(plan.get("yours"), list) else []
    if yours := listed + [hands_off_text(plan)] * bool(plan.get("hands_off")) + [by_hand_text(c) for c in plan.get("close_by_hand") or []]:
        rows.append(("yours", " · ".join(yours)))
    width = max((len(name) for name, _ in rows), default=0) + 2
    lines += [f"{name:<{width}}{text}" for name, text in rows]
    if not events and not rows:
        lines.append("nothing to do")
    return lines


def halt_banner(plan: Mapping[str, Any]) -> list[str]:
    return [f"halted: {plan['halted']}"] if plan.get("halted") else []


def event_json(plan: Mapping[str, Any]) -> list[str]:
    """One JSON line per event, for a reader of the log: `{"event": "started", "key": "ABC-2", "time": ...}`."""
    return [json.dumps({**e, "time": plan["time"]}) for e in plan["events"]]


def write_state(path: Path, plan: Mapping[str, Any]) -> None:
    """Write the plan to `path` atomically: a temporary file in the same folder, then a rename over the old file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temp.write_text(json.dumps(plan, indent=2) + "\n")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def mark_round_start(path: Path, now: float) -> None:
    """Write the start of a round into the state file, beside the last plan, which stays as it is. The round's own plan
    replaces the file at its end, so the mark goes away with it. `--watch` and `--check` read the mark (`running_text`)."""
    found: object = None
    try:
        found = json.loads(path.read_text())
    except (OSError, ValueError):
        pass
    write_state(path, {**(found if isinstance(found, dict) else {}), "round_started": _when(now), "round_pid": os.getpid()})


PID_START_SLACK = 5.0  # seconds: `ps` shows the start time in whole seconds


def _alive(pid: int) -> bool:
    """True when process `pid` exists. A process of another user (`PermissionError`) exists too."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


def _process_start(pid: int) -> float | None:
    """The start time of process `pid` as epoch seconds (`ps -o lstart=`), or None when `ps` cannot say."""
    try:
        done = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], check=False, capture_output=True, text=True, timeout=5,
                              env={**os.environ, "LC_ALL": "C", "TZ": "UTC0"})
        return _utc(done.stdout.strip(), "%a %b %d %H:%M:%S %Y")  # TZ=UTC0: no local-time step
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _is_round_process(pid: int, started: float) -> bool:
    """True when process `pid` can be the round that wrote the mark at `started`. A round's process starts before its
    mark. After a reboot the system can give the old pid to a new process, which starts after the mark (a service keeps
    its pid through each exec, so the start time stays the service's own). When `ps` cannot say, the process counts."""
    if not _alive(pid):
        return False
    begun = _process_start(pid)
    return begun is None or begun <= started + PID_START_SLACK


def running_text(plan: Mapping[str, Any], now: float, tz: Any = None) -> str | None:
    """`round running for 6 min (since 16:46)` when the state file's round start is newer than its plan, else None.
    The mark carries the process id of its round (`round_pid`): a round whose process is gone (a stop, a reboot), or
    whose id a newer process holds now (it started after the mark), shows no line. A mark with no process id (an older one) shows as before."""
    try:
        started = _utc(str(plan["round_started"]), "%Y-%m-%dT%H:%M:%SZ")
        last = _utc(str(plan["time"]), "%Y-%m-%dT%H:%M:%SZ") if plan.get("time") else None
    except (KeyError, ValueError):
        return None
    if last is not None and started <= last:
        return None
    pid = plan.get("round_pid")
    if isinstance(pid, int) and pid > 0 and not _is_round_process(pid, started):
        return None
    at = datetime.fromtimestamp(started, UTC).astimezone(tz).strftime("%H:%M")
    return f"round running for {_ago(now - started)} (since {at})"


# -- The view: `--watch` draws the state file's plan in place, for a narrow pane such as a phone's. ------------------

MARKS = {"building": ("●", "32"), "review": ("◐", "34")}  # the mark and its color: green, blue
GRAY, RED, BOLD, BOLD_YELLOW = "90", "31", "1", "1;33"


def _ago(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds} s" if seconds < 60 else f"{seconds // 60} min" if seconds < HOUR else f"{seconds // HOUR} h"


def _wrap(items: Sequence[str], width: int, first: str, rest: str) -> list[str]:
    """The items joined by commas after `first`, wrapped under `rest`'s indent; never inside an item."""
    lines: list[str] = []
    line, empty = first, True
    for n, item in enumerate(items):
        piece = item + ("," if n < len(items) - 1 else "")
        if not empty and len(line) + 1 + len(piece) > width:
            lines.append(line)
            line, empty = rest + piece, False
        else:
            line, empty = line + ("" if empty else " ") + piece, False
    return [*lines, line]


def _one_line(items: Sequence[str], width: int, first: str) -> str:
    """As many items as fit on one line after `first`, then `+N more` for the rest."""
    for shown in range(len(items), -1, -1):
        more = f" +{len(items) - shown} more" if shown < len(items) else ""
        line = first + ", ".join(items[:shown]) + more
        if len(line) <= width or shown == 0:
            return line.replace(first + " +", first + "+")
    return first  # not reached: shown == 0 returns


def render(plan: Mapping[str, Any], now: float, width: int, height: int, color: bool, tz: Any = None) -> list[str]:
    """The plan as the lines of a screen `width` wide and `height` tall: the round's local time, the counts and the plan's
    age (in a narrow pane `updated` goes first, then the counts' names `b` and `r`, then the age); each Area in bold with the issues that hold it (`●` building, `◐` in review) and its Todo queue after
    `▸`, wrapped; then `yours`: what waits on the owner with its reason (bold yellow), `pull requests`: each other open
    pull request on one line with its age and state (a stale one red), and `other`: the count of the `hands-off` issues, the other waits, the draft stacks,
    the closes by hand and, while two hosts are in use, the keys of each host's sessions (`hosts_text`).
    When the plan is taller than the screen, each queue takes one line ending in `+N more`; when it is still taller, the
    queues go, and then the lines past the screen's last (`+N more`), so the top line always shows. The marks carry the meaning, so the lines read with no color. A pure function: the
    tests need no terminal. While the state file's round start is newer than its plan and its process still runs, the first line is `round running for N min
    (since HH:MM)`."""
    def paint(text: str, code: str) -> str:
        return f"\x1b[{code}m{text}\x1b[0m" if color and code else text

    def local(stamp: str) -> str:
        return datetime.fromtimestamp(_utc(stamp, "%Y-%m-%dT%H:%M:%SZ"), UTC).astimezone(tz).strftime("%H:%M")

    top: list[str] = [paint(line[:width], RED) for line in halt_banner(plan)]
    if running := running_text(plan, now, tz):
        top.append(paint(running[:width], BOLD_YELLOW))
    if failed := plan.get("failed"):
        top.append(paint(f"{local(str(failed['time']))} · round failed: {failed['error']}", RED))
    if plan.get("time"):
        age = now - _utc(str(plan["time"]), "%Y-%m-%dT%H:%M:%SZ")
        at, mark = local(str(plan["time"])), your_count(plan)
        counts = [f"build {plan['building']}/{plan['max_runs']} · review {plan['reviewing']}", f"b {plan['building']}/{plan['max_runs']} · r {plan['reviewing']}"]
        ages = [f"updated {_ago(age)} ago", f"{_ago(age)} ago"]
        # The longest top line that fits: the word `updated` goes first, then the counts' names, and the age last (a narrow pane).
        # The age stays as long as it can: its red color shows a stalled orchestrator.
        count, tail = next(((c, t) for c in counts for t in ages if len(f"{at} · {c} · you {mark}" + (f" · {t}" if t else "")) <= width),
                           (counts[-1], ""))
        head, you = f"{at} · {count} · ", f"you {mark}"
        old = age > 3 * int(plan.get("poll_seconds") or 60)
        top.append(head + paint(you, BOLD_YELLOW if mark else "") + (" · " + paint(tail, RED if old else GRAY) if tail else ""))
    elif not top:
        top.append("no round yet")

    def areas(compact: bool, queues: bool = True) -> list[str]:
        lines: list[str] = []
        for q in plan.get("areas") or []:
            lines.append(paint(str(q["area"]), BOLD))
            for h in q["holders"]:
                mark, code = MARKS.get(str(h.get("kind")), MARKS["building"])
                lines.append(paint(f"{mark} {h['key']}", code))
            items = [str(i["key"]) + (f" {i['mark']}" if i.get("mark") else "") for i in q["queue"]]
            if items and queues:
                wrapped = [_one_line(items, width, "  ▸ ")] if compact else _wrap(items, width, "  ▸ ", "    ")
                lines += [paint(line, GRAY) for line in wrapped]
        return lines

    other = [f"  yours  {hands_off_text(plan)}"] * bool(plan.get("hands_off"))
    other += [f"  {w['key']} {w['reason']}" for w in plan.get("other_waits") or []]
    other += [f"  {s['key']} draft stack on {s['on']}" for s in plan.get("stacks") or []]
    other += [f"  {by_hand_text(c)}" for c in plan.get("close_by_hand") or []]
    other += [f"  {line}" for line in hosts_text(plan)]

    def cut(line: str) -> str:
        return line if len(line) <= width else line[:max(1, width - 1)] + "…"

    def aged(item: Mapping[str, Any]) -> str:
        return f" ({_ago(now - _utc(str(item['since']), '%Y-%m-%dT%H:%M:%SZ'))})" if item.get("since") else ""
    yours = [(cut(f"  {your_text(y)}"), BOLD_YELLOW) for y in plan["yours"]] if isinstance(plan.get("yours"), list) else []
    prs = [(cut(f"  #{p['number']} {p['key']}{aged(p)} {pr_text(p)[len(str(p['key'])) + 1:]}"), RED if p["state"] == PR_STALE else "") for p in shown_prs(plan)]
    sections = [(name, lines) for name, lines in (("yours", yours), ("pull requests", prs), ("other", [(cut(x), "") for x in other])) if lines]
    rest = [line for name, lines in sections for line in [(name, BOLD, True), *((text, code, False) for text, code in lines)]]
    body = areas(compact=False)
    if len(top) + len(body) + len(rest) > height:
        body = areas(compact=True)
    if len(top) + len(body) > height:  # still too tall: the holders keep their lines, the queues go
        body = areas(compact=True, queues=False)
    room = height - len(top) - len(body)  # the lines left for the sections, their names included
    if len(rest) > room:  # the holders always show; the sections take the lines left, or none
        keep = rest[:max(0, room - 1)]
        while keep and keep[-1][2]:  # never a section's name with no line under it
            keep.pop()
        dropped = sum(1 for line in rest[len(keep):] if not line[2])
        rest = [*keep, (f"  +{dropped} more", "", False)] if keep else []
    return (top + body + [paint(text, code) for text, code, _ in rest])[:max(height, 1)]  # never taller: the pane would scroll


def render_all(plan: Mapping[str, Any], now: float, width: int, height: int, color: bool, tz: Any = None) -> list[str]:
    """The state file's content as a screen: one team's plan as `render` draws it; with `others`, each team apart,
    under its key in bold, the screen's height shared between them."""
    others = [o for o in plan.get("others") or [] if isinstance(o, dict)]
    if not others:
        return render(plan, now, width, height, color, tz)
    plans = [dict(plan, halted=None), *others]
    lines = [f"\x1b[{RED}m{line[:width]}\x1b[0m" if color else line[:width] for line in halt_banner(plan)]
    each = max((height - len(lines)) // len(plans), 3)
    for p in plans:
        head = str(p.get("team") or "")
        lines.append(f"\x1b[1m{head}\x1b[0m" if color else head)
        lines += render(p, now, width, each - 1, color, tz)
    return lines[:max(height, 1)]


def watch(path: Path, write: Callable[[str], None], size: Callable[[], tuple[int, int]], color: bool,
          sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.time) -> int:
    """Draw the plan of `path` each time the file or the screen changes, and each time the age changes: it reads only that
    file, never Linear or GitHub. Ctrl-C ends it."""
    seen: int | None = -1
    plan: Mapping[str, Any] | None = None
    drawn: list[str] | None = None
    try:
        while True:
            try:
                mtime: int | None = path.stat().st_mtime_ns
            except FileNotFoundError:
                mtime, plan = None, None
            if mtime is not None and mtime != seen:
                try:
                    found = json.loads(path.read_text())
                    plan = found if isinstance(found, dict) else plan
                except (OSError, ValueError):  # a file that a round replaces as it is read: the next look reads it
                    mtime = seen
            seen = mtime
            width, height = size()
            lines = render_all(plan, clock(), width, height, color) if plan else [f"no plan yet in {path}"]
            if lines != drawn:
                write("\x1b[H\x1b[2J" + "\n".join(lines))
                drawn = lines
            sleep(1)
    except KeyboardInterrupt:
        write("\n")
        return 0


# -- The pinned queue: the plan's queues in one comment of one issue, edited in place, for the owner's phone. ----

QUEUE_MARK = "Orchestrator queue"  # the first words of the queue's comment: the orchestrator finds its own comment by them


def queue_lines(plan: Mapping[str, Any], in_progress: str = "In Progress") -> list[str]:
    """The plan's grouped lines for the pinned issue: the counts, one line per Area, the other waits, the draft stacks, the
    open pull requests and the row `yours`.
    No events (they belong to one round), no round stamp and no slots line (a fresh start's age changes each minute), so
    two rounds with the same queue give the same lines. Keys,
    Area names, states and reasons only, as the plan holds them."""
    lines = plan_text(dict(plan, slots=[]), [], in_progress=in_progress)
    return [counts_text(plan), *lines[1:]]


def comment_body(mark: str, lines: Sequence[str], stamp: str) -> str:
    """The comment: one heading line with the round's time, then the lines in a code block (Linear keeps their columns)."""
    return f"{mark} at {stamp}. The orchestrator edits this comment in place.\n\n```\n" + "\n".join(lines) + "\n```\n"


def block_lines(body: str) -> list[str] | None:
    """The lines in the comment's code block, or None when it has none."""
    found = re.search(r"```[^\n]*\n(.*?)\n?```", body, re.S)
    return found.group(1).splitlines() if found else None


@dataclass(frozen=True)
class Comments:
    """The three calls of a pinned comment, each a function that raises when it fails. A test passes its own."""

    find: Callable[[str, str], tuple[str, str, str] | None]  # (issue, mark) -> (id, body, updatedAt) of its newest own comment
    update: Callable[[str, str], None]  # (comment id, body)
    create: Callable[[str, str], str]  # (issue, body) -> the new comment's id


@dataclass
class PinnedComment:
    """One comment on one issue that the orchestrator edits in place, never a new comment each round: the queue,
    and the triage's checks may take one of their own (another mark, the same issue or another). It writes only
    when the lines changed, and at most once per `min_seconds`: a change inside that time waits for a later round. The
    last lines and their time are a cache: after a restart, the first round reads them from the comment itself (the
    newest comment of this key that starts with `mark`), so a restart writes nothing new. A failed call prints its
    error's type and leaves the rest to the next round."""

    issue: str
    mark: str
    min_seconds: float
    comments: Comments | None  # None: a dry run, which prints what it would write
    comment_id: str | None = None
    lines: list[str] | None = None
    at: float | None = None
    read: bool = False

    def put(self, lines: Sequence[str], stamp: str, now: float) -> list[str]:
        """Write the lines when they changed and the time allows; the lines for the terminal and the log."""
        if self.comments is not None and not self.read:
            try:
                found = self.comments.find(self.issue, self.mark)
            except Exception as e:  # no write without the read: a create would make a second comment
                return [f"queue: reading {self.issue} failed: {type(e).__name__}"]
            self.read = True
            if found is not None:
                self.comment_id, body, updated = found
                self.lines, self.at = block_lines(body), _seconds(updated)
        def halted(rows: Sequence[str] | None) -> bool:
            return any(line.startswith("halted: ") for line in rows or [])

        if self.lines == list(lines) or (halted(lines) == halted(self.lines) and self.at is not None and now - self.at < self.min_seconds):
            return []
        body = comment_body(self.mark, lines, stamp)
        if self.comments is None:
            self.lines, self.at = list(lines), now
            return [f"would write the queue to {self.issue}:", *body.splitlines()]
        try:
            if self.comment_id is None:
                self.comment_id = self.comments.create(self.issue, body)
            else:
                self.comments.update(self.comment_id, body)
        except Exception as e:  # a comment deleted by hand: read again next round, then make a new one
            self.comment_id, self.read = None, False
            return [f"queue: writing to {self.issue} failed: {type(e).__name__}"]
        self.lines, self.at = list(lines), now
        return [f"wrote the queue to {self.issue}"]


def real_comments(cfg: Config) -> Comments:
    """The pinned comment's calls through the Linear module's `post`: `.github/linear.py` has no edit of a comment."""

    def find(key: str, mark: str) -> tuple[str, str, str] | None:
        query = """query Comments($id: String!) { issue(id: $id) { comments(first: 100) { nodes { id body updatedAt
          user { isMe } } } } }"""
        found = linear_api(cfg).post(query, {"id": key}).get("issue")
        if not isinstance(found, dict):
            raise RuntimeError(f"no issue {key}")
        own = [c for c in (found.get("comments") or {}).get("nodes") or []
               if (c.get("user") or {}).get("isMe") and str(c.get("body") or "").startswith(mark)]
        newest = max(own, key=lambda c: str(c.get("updatedAt") or ""), default=None)
        return (str(newest["id"]), str(newest["body"]), str(newest.get("updatedAt") or "")) if newest else None

    def update(comment: str, body: str) -> None:
        mutation = "mutation Edit($id: String!, $body: String!) { commentUpdate(id: $id, input: { body: $body }) { success } }"
        if not linear_api(cfg).post(mutation, {"id": comment, "body": body}).get("commentUpdate", {}).get("success"):
            raise RuntimeError("commentUpdate refused")

    def create(key: str, body: str) -> str:
        api = linear_api(cfg)
        mutation = """mutation Comment($id: String!, $body: String!) { commentCreate(input: { issueId: $id, body: $body }) {
          success comment { id } } }"""
        done = api.post(mutation, {"id": api.issue(key)["id"], "body": body}).get("commentCreate", {})
        if not done.get("success"):
            raise RuntimeError("commentCreate refused")
        return str(done["comment"]["id"])

    return Comments(find=find, update=update, create=create)


def make_queue(cfg: Config, dry: bool) -> PinnedComment | None:
    """The queue's pinned comment, or None with no `queue_issue`. A dry run reads and writes nothing in Linear."""
    if not cfg.queue_issue:
        return None
    return PinnedComment(cfg.queue_issue, QUEUE_MARK, cfg.queue_write_minutes * 60, None if dry else real_comments(cfg))


# -- Session hosts: where a run opens, so the owner sees it beside the owner's own sessions. ------------------------------

Run = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


def run_quiet(argv: Sequence[str], env: Mapping[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(argv), capture_output=True, text=True, timeout=GH_TIMEOUT, check=False,
                          env=None if env is None else dict(env))


def keychain_password(item: str, run: Run = run_quiet) -> str:
    """The password in the keychain item `item` of the orchestrator's user (`security find-generic-password -s ITEM -w`), read
    at each call: a daemon's new password needs no restart. The error names the item and the exit code, never the output."""
    if not PASSWORD_ITEM_SHAPE.fullmatch(item):
        raise RuntimeError(f"refused to read the keychain item {item!r}")
    done = run(["/usr/bin/security", "find-generic-password", "-s", item, "-w"])
    password = (done.stdout or "").rstrip("\n")
    if done.returncode != 0 or not password:
        raise RuntimeError(f"cannot read the keychain item {item} (security exited {done.returncode})")
    return password


class LaunchUnknown(RuntimeError):
    """The host cannot yet tell whether the launch created a session."""

    def __init__(self, confirm: Callable[[], str | None]) -> None:
        super().__init__("the host could not confirm the launch")
        self.confirm = confirm


class HerdrHost:
    """A run is a new workspace on the local herdr server, with the command typed into its shell: the owner's herdr shows
    it as a machine, and the owner answers or takes over the run there. The pane belongs to the herdr server, so a restart
    of the orchestrator does not end it. Checked by hand as the agents' user, 2026-11-10."""

    def __init__(self, run: Run = run_quiet) -> None:
        self.run = run

    def _json(self, *argv: str) -> dict[str, Any]:
        done = self.run(["herdr", *argv])
        if done.returncode != 0:
            raise RuntimeError(f"herdr {argv[0]} {argv[1]} failed: {(done.stderr or done.stdout).strip()[:300]}")
        found = json.loads(done.stdout or "{}")
        return found if isinstance(found, dict) else {}

    def start(self, label: str, command: str, cwd: str) -> str:
        agent, *args = shlex.split(command)
        made = self._json("workspace", "create", "--label", label, "--cwd", cwd, "--no-focus")
        pane = str(made["result"]["root_pane"]["pane_id"])
        name = f"orchestrator-{pane.lower().replace(':', '-')}"
        try:
            # `agent start` waits for agent detection. `pane run` only submits the shell input.
            self._json("agent", "start", name, "--kind", agent, "--pane", pane, "--", *args)
        except Exception:
            space = str(made["result"]["workspace"]["workspace_id"])

            def confirm() -> str | None:
                try:
                    found = self._json("pane", "get", pane)["result"]["pane"]
                except Exception:
                    if any(w.get("workspace_id") == space for w in self.workspaces()):
                        raise
                    return None  # the new workspace is already gone
                if found.get("agent") or found.get("agent_session"):
                    return pane
                self._json("workspace", "close", space)
                return None

            try:
                session = confirm()
            except Exception as e:
                raise LaunchUnknown(confirm) from e
            if session is None:
                raise
        return pane

    def alive(self, session: str) -> bool:
        """The command still runs: the pane's foreground process group is not its shell's. A closed pane is not alive."""
        try:
            info = self._json("pane", "process-info", "--pane", session)["result"]["process_info"]
        except (RuntimeError, KeyError, ValueError):
            return False
        return bool(info.get("foreground_process_group_id") != info.get("shell_pid"))

    def failed(self, session: str) -> bool:
        """herdr cannot tell a setup failure from another stop: never."""
        return False

    def tail(self, session: str) -> str:
        done = self.run(["herdr", "pane", "read", session, "--lines", "60", "--source", "recent"])
        return done.stdout if done.returncode == 0 else f"(herdr could not read the pane: {done.stderr.strip()[:200]})"

    def workspaces(self) -> list[dict[str, Any]]:
        """Every workspace of the herdr server: workspace_id, label and agent_status, among others."""
        return read_sessions(self.run)

    def wake(self, key: str, text: str) -> bool:
        """Type `text` and Enter into the root pane of the key's newest workspace (`pane run`), only when herdr shows the
        agent in that pane `idle` or `done`: a busy agent would take the text as its next input in the middle of a
        tool call. A pi pane shows `done` when its turn ends, and `idle` only once someone has looked at it (measured 2026-11-23), and the orchestrator's panes open with no focus. False when there is no such workspace, its pane is gone, or
        the agent is not at rest. `text` is one line: a newline would
        send the message in parts."""
        if "\n" in text:
            raise ValueError("a wake message is one line")
        spaces = [s for s in self.workspaces() if own_key(s.get("label")) == key]
        if not spaces:
            return False
        space = str(max(spaces, key=lambda s: session_number(s.get("label"))).get("workspace_id") or "")
        if not AGENT_ID.fullmatch(space):
            return False
        try:
            panes = self._json("pane", "list", "--workspace", space)["result"]["panes"]
        except (RuntimeError, KeyError, ValueError):
            return False  # the workspace is gone
        pane = panes[0] if panes and isinstance(panes[0], dict) else {}
        pane_id = str(pane.get("pane_id") or "")
        # A pane with no agent (its claude ended, back at the shell) lists `agent` null and the status `unknown`, never `idle`
        # (read live, 2026-11-17); the agent is required too, so a line is never typed into a shell
        if not pane.get("agent") or pane.get("agent_status") not in ("idle", "done") or not AGENT_ID.fullmatch(pane_id):
            return False
        try:
            self._json("pane", "run", pane_id, text)
        except RuntimeError:
            return False  # the pane closed since the list
        return True

    def paused(self) -> dict[str | None, tuple[str, str]]:
        """herdr cannot see why a turn stopped: never a pause."""
        return {}

    def close(self, workspace: str) -> None:
        done = self.run(["herdr", "workspace", "close", workspace])
        if done.returncode != 0:
            raise RuntimeError(f"herdr workspace close failed: {(done.stderr or done.stdout).strip()[:300]}")


# Paseo's agent states, as herdr names them (`BUSY`, `FINISHED`): an agent in error has ended, and may close. A closed
# agent is one whose process ended (a daemon restart closes every idle agent): an archived agent does not appear without `-a`,
# so each closed agent in the list is finished and not archived yet. An agent that is starting (`initializing`) is busy, so a restart does
# not start its Todo issue again. These are all five of Paseo's lifecycle statuses (`AGENT_LIFECYCLE_STATUSES`, CLI 0.15.4): an agent
# that waits at a permission question keeps `running`. Any other value is "unknown" and stays open. The same five on CLI 0.16.1,
# where `error` is also a turn that an API error stopped (a usage limit, an invalid key: the harness's `stop_patterns`).
PASEO_STATES = {"initializing": "working", "running": "working", "idle": "idle", "error": "done", "closed": "done"}
# A pi agent (measured on Paseo 0.16.2 and pi 1.5.2): `running` during a turn, `idle` after it, `error` after a
# turn that an API error stopped. A wake (`paseo send ID --no-wait --prompt-file FILE`) to an idle pi agent reaches it: the
# agent runs the message as its next turn, then goes back to `idle`. The end of its pi process cannot be read: an agent whose process ended while it was idle
# stays `idle` (not `closed`) until the next message, whose `paseo send` then fails with `Pi process exited`; the agent then shows `error`.
PASEO_WAKEABLE = {"idle", "closed", "error"}  # the statuses that a wake may send to; `running` and `initializing` never
# The stops of a turn that the next session hits too: since CLI 0.16 (#8480) a Claude turn that stops on an API
# error ends in `error`, and `paseo logs` shows the error as a `[System Error]` line. The words of such a stop are the
# harness's: the `stop_patterns` of the [harnesses.NAME] table of the harness of the agent's Paseo `provider` (the part before the first `/`;
# with a mix, the profile in force's table is only the fallback; Claude Code's are in
# orchestrator.example.toml), matched without regard to case. A line that matches none (a crash, `Claude stopped
# unexpectedly`; a temporary `server_error`) pauses nothing, so a wrong word fails safe: the loop acts as it did before.
PASEO_SYSTEM_ERROR = "[System Error]"
ISSUE_KEY = re.compile(r"[A-Z][A-Z0-9]{0,9}-[0-9]{1,7}")
WORKSPACE_PREFIX = "workspace:"  # a Paseo workspace's id in `PaseoHost.workspaces` is `workspace:KEY:ID`, so `close` tells it from an agent's
AGENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")  # an agent id goes into the argv of `paseo`: no option, no space


class PaseoHost:
    """A run is an agent of a Paseo daemon, the local one or the one at the product's session endpoint (`call`, `Endpoint`):
    the daemon starts the agent itself, so `run_command` is the
    whole `paseo run --no-wait --quiet ...` command, with `{label}` as its title, and its output is the agent's id. The
    owner's Paseo app shows the agent beside the owner's own sessions. The agent belongs to the daemon, so a restart of the
    orchestrator does not end it. Read from the source of the CLI 0.15.4, run as the agents' user in the live try of 2026-11-11,
    and measured again on CLI 0.16.1 on 2026-11-17."""

    def __init__(self, run: Callable[..., subprocess.CompletedProcess[str]] = run_quiet, stops: Sequence[tuple[str, str, bool]] = (),
                 by_provider: Mapping[str, Sequence[tuple[str, str, bool]]] | None = None, endpoint: Endpoint = LOCAL,
                 password: Callable[[str], str] = keychain_password) -> None:
        self.run = run
        self.endpoint = endpoint  # the daemon that every call goes to (`call`)
        self.password = password  # reads the endpoint's password item at each call
        self.stops = self.compile(stops)  # the harness's `stop_patterns`, for an agent whose provider has none of its own
        # The `stop_patterns` of each harness that a cell runs on, by the part of Paseo's `provider` of the agent before the
        # first `/`, which is the harness's name (`claude`, `pi`: RUN_TEMPLATES): with two harnesses a pi agent's error never goes through Claude Code's words
        self.by_provider = {name: self.compile(found) for name, found in (by_provider or {}).items()}
        self.retry: set[str] = set()  # the keys whose workspace archive failed after the agent's archive went through
        self.empty_starts: set[str] = set()  # exact workspace IDs from failed starts

    @staticmethod
    def compile(stops: Sequence[tuple[str, str, bool]]) -> list[tuple[re.Pattern[str], str]]:
        return [(re.compile(pattern, re.IGNORECASE), cause) for pattern, cause, _ in stops]

    def call(self, *args: str) -> subprocess.CompletedProcess[str]:
        """One `paseo` command at the endpoint: `paseo ARGS` for the local daemon, as before, else `paseo --host HOST ARGS`.
        With a password item, the password goes into this one call's environment as PASEO_PASSWORD, which the CLI reads:
        never into its command line, which `ps` shows, and never into a file. A password that cannot be read raises."""
        if not self.endpoint.host:
            return self.run(["paseo", *args])
        argv = ["paseo", "--host", self.endpoint.host, *args]
        if not self.endpoint.password_item:
            return self.run(argv)
        return self.run(argv, env={**os.environ, PASSWORD_VARIABLE: self.password(self.endpoint.password_item)})

    def start(self, label: str, command: str, cwd: str) -> str:
        key = own_key(label)
        argv = shlex.split(command)
        if not key or argv[:2] != ["paseo", "run"]:
            raise ValueError("invalid Paseo start")
        made = self.call("workspace", "create", "--isolation", "local", "--path", cwd, "--title", key, "-q")
        space = made.stdout.strip()
        if made.returncode or not AGENT_ID.fullmatch(space):
            raise RuntimeError("Paseo did not return a new workspace ID")
        try:
            done = self.call(argv[1], "--workspace", space, *argv[2:])
            if done.returncode:
                raise RuntimeError(f"paseo run failed: {(done.stderr or done.stdout).strip()[-300:]}")
        except Exception as problem:
            error = problem  # keep the error if the next round needs to confirm the launch

            def confirm() -> str | None:
                occupants = self._occupants(space)
                if occupants:
                    return next((str(a.get("id") or label) for a in occupants if a.get("name") == label), None)
                try:
                    self._archive_workspace(space)
                except Exception:
                    self.empty_starts.add(space)
                    error.add_note("workspace cleanup failed")
                return None

            try:
                session = confirm()
            except Exception as e:
                raise LaunchUnknown(confirm) from e
            if session is None:
                raise
            return session
        session = done.stdout.strip()
        # A successful run reply means the agent exists, even if the CLI could not print its ID.
        return session if AGENT_ID.fullmatch(session) else label

    def _occupants(self, space: str) -> list[dict[str, Any]]:
        # Paseo 0.17.1 reports workspaceId, including for background agents. Older replies cannot prove it is empty.
        agents = self._agents(background=True)
        if any("workspaceId" not in agent for agent in agents):
            raise RuntimeError("Paseo did not report agent workspace IDs")
        return [agent for agent in agents if agent["workspaceId"] == space]

    def clean_failed_starts(self) -> list[str]:
        for space in sorted(self.empty_starts):
            try:
                if not self._occupants(space):
                    self._archive_workspace(space)
            except Exception:
                continue  # retain this exact ID for the next round
            self.empty_starts.remove(space)
        return ["Paseo: failed start workspace cleanup still pending"] if self.empty_starts else []

    def _agents(self, *, background: bool = False) -> list[dict[str, Any]]:
        """Every agent of the daemon that is not archived, of every folder (`-g`)."""
        done = self.call("ls", "-g", "--json", *(["--background"] if background else []))
        if done.returncode != 0:
            raise RuntimeError(f"paseo ls failed: {(done.stderr or done.stdout).strip()[:300]}")
        found = json.loads(done.stdout or "[]")
        if not isinstance(found, list):
            raise RuntimeError("paseo ls gave no list")
        return [a for a in found if isinstance(a, dict)]

    def alive(self, session: str) -> bool:
        """The agent still works: its status is `running`. `session` is the agent's id, its short id, or its title (the label
        that stands for an id that `start` could not read). A failed list, or an agent that is gone, is not alive."""
        try:
            agents = self._agents()
        except (RuntimeError, ValueError):
            return False
        return any(session in (a.get("id"), a.get("shortId"), a.get("name")) and a.get("status") == "running"
                   for a in agents)

    def failed(self, session: str) -> bool:
        """The agent stopped in `error`: a start whose agent is in error before its claim is a setup failure. A
        failed list raises, so the round reads again and never halts on a list it could not read."""
        return any(session in (a.get("id"), a.get("shortId"), a.get("name")) and a.get("status") == "error"
                   for a in self._agents())

    def wake(self, key: str, text: str) -> bool:
        """Send `text` to the key's newest agent with `paseo send ID --no-wait --prompt-file FILE`, only when its
        status is idle, closed or error. A running or starting agent gets nothing: `send` steers an active turn by default,
        which stops the running tool call at once (a `git push`, a gates run). The agent comes from the list without `-a`, so
        an archived agent is never found: a send would un-archive it (measured 2026-11-16, CLI 0.15.4, and again on 0.16.1). The newest agent is the youngest by `created_age`, then the largest `session_number`: `created` is a relative
        text ("3 days ago"), and the label's number starts again at 1 after a restart of the orchestrator. False when there is no
        agent, it is busy, the daemon answers `Agent not found`, or a pi agent's process ended (`Pi process exited`); any other failure raises. Exit 0 proves nothing about the
        fix: the draft claim does. An agent woken from `error` keeps its recorded `--env` (`hidden_keys`): measured 2026-11-23
        (CLI 0.17.1) on a scratch daemon with `PROBE=x` in its environment, an agent started with `--env PROBE=`
        and sent a message while in `error`: the new `claude` process had `PROBE=` (empty), the daemon kept `PROBE=x`. So a
        wake from `error` needs no `--env` flags and no fresh agent. The same holds for an agent in `closed` (measured 2026-11-23, CLI 0.17.1): after a daemon restart the agent had no process, and
        the wake's new process had `PROBE=` again, while a control agent started with no `--env` got the daemon's `PROBE=x`. A turn that stopped on an API error is `error` too (0.16.1): `pause_events` holds the fix
        wakes back until the pause ends, and the one retry of a stop (`Retry`) is the wake it gets."""
        agents = [a for a in self._agents() if own_key(a.get("name")) == key]
        if not agents:
            return False
        now = time.time()  # one clock for the list: two agents of one ISO time tie, and the label's number decides
        newest = min(agents, key=lambda a: (created_age(a.get("created"), now), -session_number(a.get("name"))))
        agent = str(newest.get("id") or "")
        if newest.get("status") not in PASEO_WAKEABLE or not AGENT_ID.fullmatch(agent):
            return False
        with tempfile.TemporaryDirectory(prefix="wake-") as folder:
            file = Path(folder) / "message.txt"
            file.write_text(text, encoding="utf-8")
            done = self.call("send", agent, "--no-wait", "--prompt-file", str(file))
        if done.returncode == 1 and any(w in f"{done.stderr}{done.stdout}" for w in ("Agent not found", "Pi process exited")):
            return False  # no session to wake: the agent's process ended
        if done.returncode != 0:
            raise RuntimeError(f"paseo send failed: {(done.stderr or done.stdout).strip()[:300]}")
        return True

    def paused(self) -> dict[str | None, tuple[str, str]]:
        """Each profile's stop: profile -> (key, cause), for the newest agent of the orchestrator's label of each
        profile (the agent's label `profile=NAME`, which `PASEO_START` gives) that stopped on an API error that the next session
        of the profile hits too. The key `None` is an agent with no profile label (one that an older start or the owner
        made): it counts only when it is the newest of all, and then it stands for every profile. A profile's agent stopped when
        its status is `error` and the last `[System Error]` line of `paseo logs ID --tail 20` matches the `stop_patterns` of the
        agent's own harness (`by_provider`, by the part of Paseo's `provider` of the agent before the first `/`; `stops`, the profile in force's, when the
        provider has none). A profile whose newest agent did not stop that way has no entry. The newest is the youngest by
        `created_age`; of two of the same age, one that is not in error. A failed list or log read raises, so the round keeps
        the pauses as they were."""
        agents = [a for a in self._agents() if own_key(a.get("name"))]
        now = time.time()

        def newest(of: Sequence[dict[str, Any]]) -> dict[str, Any]:
            return min(of, key=lambda a: (created_age(a.get("created"), now), a.get("status") == "error"))

        groups = {profile: [a for a in agents if agent_profile(a) == profile] for profile in {agent_profile(a) for a in agents} - {None}}
        if agents and agent_profile(latest := newest(agents)) is None:
            groups[None] = [latest]
        found: dict[str | None, tuple[str, str]] = {}
        for profile, group in groups.items():
            if stop := self._stop(newest(group)):
                found[profile] = stop
        return found

    def _stop(self, agent: dict[str, Any]) -> tuple[str, str] | None:
        """(key, cause) when this agent stopped on an API error with a cause of its harness's `stop_patterns`, else None."""
        agent_id = str(agent.get("id") or "")
        if agent.get("status") != "error" or not AGENT_ID.fullmatch(agent_id):
            return None
        done = self.call("logs", agent_id, "--tail", "20")
        if done.returncode != 0:
            raise RuntimeError(f"paseo logs failed: {(done.stderr or done.stdout).strip()[:300]}")
        errors = [line.partition(PASEO_SYSTEM_ERROR)[2] for line in (done.stdout or "").splitlines() if PASEO_SYSTEM_ERROR in line]
        if not errors:
            return None
        harness = str(agent.get("provider") or "").partition("/")[0]  # Paseo lists `claude/claude-sonnet-5-5`: the harness is the part before the slash
        stops = self.by_provider.get(harness, self.stops)  # the words of the agent's own harness
        cause = next((name for pattern, name in stops if pattern.search(errors[-1])), None)
        key = own_key(agent.get("name"))
        return (key, cause) if cause and key else None

    def tail(self, session: str) -> str:
        done = self.call("logs", session, "--tail", "60")
        return done.stdout if done.returncode == 0 else f"(paseo could not read the agent: {done.stderr.strip()[:200]})"

    def workspaces(self) -> list[dict[str, Any]]:
        """Every agent that is not archived, in herdr's shape: workspace_id (the agent's id), label (its title) and
        agent_status (`PASEO_STATES`). Then each Paseo workspace whose archive failed after its agent's archive went through
        (`self.retry`: the issue keys), when no agent of that key is left: its workspace_id is `workspace:KEY:ID`, its
        `key` the issue's, its status `done`, so `to_close` and `close` try it again. Only a workspace that this host failed
        to archive is listed, never one that the owner opened. A failed workspace list gives no such entry. `self.retry` lives in
        memory only: a restart of the orchestrator forgets it, and a workspace whose archive failed then stays until the owner
        archives it (as `Runner`'s start table does). Paseo's JSON cannot tie an agent to its workspace (`paseo ls -g --json` has
        `cwd`, the same for every agent of the checkout, and no workspace id; measured 2026-11-12, CLI 0.15.4), and `paseo
        workspace archive` archives "everything it owns": so an agent of the owner's inside a remembered workspace cannot be
        told, and `have` sees only the agents with an orchestrator label. The same on CLI 0.16.1: the workspace id is
        only in the agent's environment (`PASEO_WORKSPACE_ID`), never in `ls` or `inspect`."""
        agents = self._agents()
        out = [{"workspace_id": str(a.get("id") or ""), "label": str(a.get("name") or ""),
                "agent_status": PASEO_STATES.get(str(a.get("status") or ""), "unknown")}
               for a in agents]
        if not self.retry:
            return out
        have = {k for a in agents if (k := own_key(a.get("name")))}
        try:
            found = self._workspace_list()
        except (RuntimeError, ValueError):
            return out
        named = {str(w.get("name") or "").upper() for w in found}
        self.retry &= named  # a workspace that is gone (the owner archived it) needs no more tries
        for space in found:
            name = str(space.get("name") or "").upper()
            space_id = str(space.get("workspaceId") or "")
            if name in self.retry and name not in have and space_id:
                out.append({"workspace_id": f"{WORKSPACE_PREFIX}{name}:{space_id}", "label": name, "key": name, "agent_status": "done"})
        return out

    def _workspace_list(self) -> list[dict[str, Any]]:
        done = self.call("workspace", "ls", "--json")
        if done.returncode != 0:
            raise RuntimeError(f"paseo workspace ls failed: {(done.stderr or done.stdout).strip()[:300]}")
        found = json.loads(done.stdout or "[]")
        if not isinstance(found, list):
            raise RuntimeError("paseo workspace ls gave no list")
        return [w for w in found if isinstance(w, dict)]

    def _archive_workspace(self, space_id: str) -> None:
        if not AGENT_ID.fullmatch(space_id):
            raise RuntimeError(f"refused to archive the workspace {space_id!r}")
        done = self.call("workspace", "archive", space_id)
        if done.returncode != 0:
            raise RuntimeError(f"paseo workspace archive failed: {(done.stderr or done.stdout).strip()[:300]}")

    def close(self, workspace: str) -> None:
        """An archive keeps the agent's history; a delete would not. Then the Paseo workspace named after the agent's issue key
        is archived too (the app's sidebar lists workspaces, and an archived agent leaves its workspace empty), unless
        an agent of that key still runs. A workspace of another name is never archived. An id `workspace:KEY:ID` is a workspace
        that `workspaces` found with no agent left: only it is archived."""
        if workspace.startswith(WORKSPACE_PREFIX):
            kept, _, space_id = workspace[len(WORKSPACE_PREFIX):].partition(":")
            self._archive_workspace(space_id)
            self.retry.discard(kept)
            return
        if not AGENT_ID.fullmatch(workspace):
            raise RuntimeError(f"refused to archive the agent {workspace!r}")
        agents = self._agents()  # before the archive: an archived agent leaves the list
        key = next((own_key(a.get("name")) for a in agents if workspace in (a.get("id"), a.get("shortId"))), None)
        done = self.call("archive", workspace)
        if done.returncode != 0:
            raise RuntimeError(f"paseo archive failed: {(done.stderr or done.stdout).strip()[:300]}")
        if key is None or any(own_key(a.get("name")) == key and a.get("status") == "running"
                              and workspace not in (a.get("id"), a.get("shortId")) for a in agents):
            return
        self.retry.add(key)  # until the workspace is archived: a failure below leaves the key here
        for space in self._workspace_list():
            if str(space.get("name") or "").upper() == key:
                self._archive_workspace(str(space.get("workspaceId") or ""))
        self.retry.discard(key)


class PaseoEndpoints:
    """The Paseo host of products of more than one session endpoint (`make_host`): one `PaseoHost` for each endpoint, and
    each call goes to the endpoint of its product. A start, a wake and a key's workspace go to the endpoint of the key's
    team; a session or a workspace id goes to the endpoint that started or listed it. The list is every endpoint's, as one
    daemon's list was every product's: the Runners, `Hosts` and `Stops` see one host. A list fails when one endpoint does not
    answer, so no round decides on half a list. The stops of every endpoint are read; for one profile, the first endpoint's
    stop counts (the products' order)."""

    def __init__(self, by_team: Mapping[str, PaseoHost], default: PaseoHost) -> None:
        self.by_team = {team.upper(): host for team, host in by_team.items()}
        self.default = default  # the first product's endpoint: a key of no team
        self.hosts = list(dict.fromkeys([default, *self.by_team.values()]))  # each endpoint once, the first product's first
        self.where: dict[str, PaseoHost] = {}  # a session or workspace id -> the endpoint that started or listed it

    def of_key(self, key: str | None) -> PaseoHost:
        return self.by_team.get((key or "").partition("-")[0].upper(), self.default)

    def of_session(self, session: str) -> PaseoHost:
        return self.where.get(session) or self.of_key(own_key(session))  # a start's label stands for an id it could not read

    def local(self) -> PaseoHost | None:
        """The endpoint of the local daemon, which the service keeps on the profile in force (`PaseoDaemon`), or None."""
        return next((h for h in self.hosts if not h.endpoint.host), None)

    def start(self, label: str, command: str, cwd: str) -> str:
        host = self.of_key(own_key(label))
        try:
            session = host.start(label, command, cwd)
        except LaunchUnknown as e:
            probe = e.confirm

            def confirm() -> str | None:
                if (found := probe()) is not None:
                    self.where[found] = host
                return found

            raise LaunchUnknown(confirm) from e
        self.where[session] = host
        return session

    def alive(self, session: str) -> bool:
        return self.of_session(session).alive(session)

    def failed(self, session: str) -> bool:
        return self.of_session(session).failed(session)

    def tail(self, session: str) -> str:
        return self.of_session(session).tail(session)

    def workspaces(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for host in self.hosts:
            for entry in host.workspaces():
                self.where[str(entry.get("workspace_id") or "")] = host
                out.append(entry)
        return out

    def close(self, workspace: str) -> None:
        if workspace.startswith(WORKSPACE_PREFIX):
            return self.of_key(workspace[len(WORKSPACE_PREFIX):].partition(":")[0]).close(workspace)
        return self.of_session(workspace).close(workspace)

    def wake(self, key: str, text: str) -> bool:
        return self.of_key(key).wake(key, text)

    def paused(self) -> dict[str | None, tuple[str, str]]:
        found: dict[str | None, tuple[str, str]] = {}
        for host in self.hosts:
            for profile, stop in host.paused().items():
                found.setdefault(profile, stop)
        return found

    def clean_failed_starts(self) -> list[str]:
        return [line for host in self.hosts for line in host.clean_failed_starts()]


class ProcessHost:
    """A run is a detached process (a new session: a restart of the orchestrator does not end it), its output in a file.
    For a host with no herdr, and for the tests."""

    def __init__(self, log_dir: Path) -> None:
        self.log_dir = log_dir
        self.procs: dict[str, subprocess.Popen[bytes]] = {}

    def start(self, label: str, command: str, cwd: str) -> str:
        """The session's id is its log file's name, `KEY-YYYYMMDDTHHMMSS` (`-N` when two starts of a key share a second): the
        label is the same for two starts of a key, so it cannot name a file."""
        self.log_dir.mkdir(parents=True, exist_ok=True)
        base = f"{(own_key(label) or 'run')}-{time.strftime('%Y%m%dT%H%M%S', time.gmtime())}"
        session, n = base, 1
        while session in self.procs or (self.log_dir / f"{session}.out").exists():
            n += 1
            session = f"{base}-{n}"
        path = self.log_dir / f"{session}.out"
        with path.open("wb") as sink, path.open("rb") as output:
            proc = subprocess.Popen(shlex.split(command), cwd=cwd, stdout=sink, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, start_new_session=True)
            deadline = time.monotonic() + GH_TIMEOUT
            try:
                while True:
                    ended = proc.poll() is not None
                    while line := output.readline():
                        if not line.endswith(b"\n"):
                            output.seek(-len(line), os.SEEK_CUR)
                            break  # the harness has not finished this line yet
                        try:
                            event = json.loads(line)
                        except ValueError:
                            continue
                        if isinstance(event, dict) and (event.get("type"), event.get("subtype")) == ("system", "init"):
                            self.procs[session] = proc
                            return session  # even if the harness already exited after initialization
                    if ended:
                        raise RuntimeError("the harness exited before reporting its session")
                    if time.monotonic() >= deadline:
                        proc.kill()
                        proc.wait()
                        continue  # read any final output before deciding whether the harness started
                    time.sleep(0.05)
            except BaseException:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
                raise

    def alive(self, session: str) -> bool:
        proc = self.procs.get(session)
        return proc is not None and proc.poll() is None

    def failed(self, session: str) -> bool:
        """A process's output is no status: never a setup failure."""
        return False

    def tail(self, session: str) -> str:
        out = self.log_dir / f"{session}.out"
        return "\n".join(out.read_text(errors="replace").splitlines()[-60:]) if out.exists() else ""

    def workspaces(self) -> list[dict[str, Any]]:
        """A process has no workspace: its output file stays, and nothing closes."""
        return []

    def wake(self, key: str, text: str) -> bool:
        """A detached process has no input to send to."""
        return False

    def paused(self) -> dict[str | None, tuple[str, str]]:
        """A process's output is no status: never a pause."""
        return {}

    def close(self, workspace: str) -> None:
        raise RuntimeError("the process host has no workspace to close")


class Host(Protocol):
    def start(self, label: str, command: str, cwd: str) -> str: ...
    def alive(self, session: str) -> bool: ...
    def failed(self, session: str) -> bool: ...  # the session stopped in error before its claim
    def tail(self, session: str) -> str: ...
    def workspaces(self) -> list[dict[str, Any]]: ...
    def close(self, workspace: str) -> None: ...
    def wake(self, key: str, text: str) -> bool: ...  # a message to the key's newest session, only when it is idle
    def paused(self) -> dict[str | None, tuple[str, str]]: ...  # profile -> (key, cause) while that profile's newest session stopped on an API error


def harness_stops(cfg: Config) -> dict[str, tuple[tuple[str, str, bool], ...]]:
    """The `stop_patterns` of each harness that a run of the config uses: the profile in force's, then those of the
    other profiles that a cell names, by the harness's name, which is the part of Paseo's `provider` of its agents before the first `/`."""
    return {cfg.harness: cfg.stop_patterns, **{run.harness[0]: run.harness[2] for _, run in cfg.cell_runs}}


def make_host(cfg: Config, password: Callable[[str], str] = keychain_password) -> Host:
    """The host of `session_host`. On Paseo, one `PaseoHost` for each session endpoint of the products: one endpoint (the
    local daemon when no product names `paseo_host`, as before) gives that host itself, several give `PaseoEndpoints`."""
    if cfg.session_host == "herdr":
        return HerdrHost()
    if cfg.session_host == "paseo":
        made: dict[tuple[str, str], PaseoHost] = {}
        by_team: dict[str, PaseoHost] = {}
        for c in team_configs(cfg):
            where = (c.endpoint.host, c.endpoint.password_item)
            if where not in made:
                made[where] = PaseoHost(stops=cfg.stop_patterns, by_provider=harness_stops(cfg), endpoint=c.endpoint, password=password)
            by_team[c.team] = made[where]
        first = by_team[cfg.team]
        return first if len(made) == 1 else PaseoEndpoints(by_team, first)
    return ProcessHost(Path(cfg.log_dir).expanduser())


def local_paseo(host: Host) -> PaseoHost | None:
    """The Paseo host of the local daemon among the host in force, which the service keeps on the profile in force
    (`PaseoDaemon`): None when no product's sessions run there (each names `paseo_host`) or the host is no Paseo host."""
    host = primary_of(host)
    if isinstance(host, PaseoEndpoints):
        return host.local()
    return host if isinstance(host, PaseoHost) and not host.endpoint.host else None


LISTED_HOSTS = ("herdr", "paseo")  # the hosts that list their sessions: only they can be an old host


class Hosts:
    """The host in force (`session_host`) and each old host that a recorded open session uses: a switch of
    `session_host` needs no wait. Each new start goes to the host in force. A wake, a close and a read of a stop go to the
    session's own host. Each session of the orchestrator's label that a host lists is recorded with its host, its key and
    when a list first showed it (`record`). The state file keeps the record as `hosts`, so the service that a switch
    restarts finds the old host's sessions. The record is not in the run line: the host is each developer's own view of
    the runs and Linear is every developer's, and the run line of a fix session has no start token.
    An old host leaves when its list shows no session of the orchestrator's label: its last session closed (`to_close`).
    The service never stops or starts an old host (Paseo's daemon: `daemon_step` acts on the host in force only).
    Only the host in force fails a list: an old host that cannot be reached leaves its sessions out of `workspaces`, so
    nothing shows them busy, a wake to them fails as before, and a fix that no session took gets one new session on the
    host in force after `claim_wait_seconds`. It keeps its record, and leaves after `drop_after` seconds with
    no answer. A plain process lists nothing, so it is never an old host."""

    def __init__(self, name: str, primary: Host, make: Callable[[str], Host], record: object = None,
                 clock: Callable[[], float] = time.time, drop_after: float = 12 * HOUR) -> None:
        self.name, self.primary, self.make, self.clock, self.drop_after = name, primary, make, clock, drop_after
        self.seen: dict[str, list[tuple[str, str, float]]] = {name: []}  # host -> (key, id, first seen) of its sessions
        self.unreached: dict[str, float] = {}  # old host -> when its list first failed
        self.where: dict[str, str] = {}  # workspace id -> its host, from the last list: `close`
        found = record if isinstance(record, dict) else {}
        for row in found.get("sessions") or []:
            host, key, space = (str(row.get(k) or "") for k in ("host", "key", "id")) if isinstance(row, dict) else ("", "", "")
            at = _seconds(row.get("seen")) if isinstance(row, dict) else None
            if (host == name or host in LISTED_HOSTS) and key and space and at is not None:
                self.seen.setdefault(host, []).append((key, space, at))
        unreached = found.get("unreached")
        for host, stamp in (unreached.items() if isinstance(unreached, dict) else ()):
            if host in self.seen and host != name and (at := _seconds(stamp)) is not None:
                self.unreached[host] = at
        self.others: dict[str, Host] = {host: make(host) for host in self.seen if host != name}

    def _host(self, name: str) -> Host:
        return self.primary if name == self.name else self.others[name]

    def start(self, label: str, command: str, cwd: str) -> str:
        return self.primary.start(label, command, cwd)

    def alive(self, session: str) -> bool:  # `Runner.live` holds only this process's starts: the host in force
        return self.primary.alive(session)

    def failed(self, session: str) -> bool:
        return self.primary.failed(session)

    def tail(self, session: str) -> str:
        return self.primary.tail(session)

    def workspaces(self) -> list[dict[str, Any]]:
        """Every host's list, each entry with its `host`. A failed list of the host in force raises, as before; an old
        host's is left out, and its record stays."""
        now, out, where = self.clock(), [], {}
        for name, host in [(self.name, self.primary), *self.others.items()]:
            try:
                listed = host.workspaces()
            except Exception:
                if name == self.name:
                    raise
                self.unreached.setdefault(name, now)
                continue
            self.unreached.pop(name, None)
            first = {space: at for _, space, at in self.seen.get(name, [])}
            rows = []
            for entry in listed:
                space = str(entry.get("workspace_id") or "")
                where[space] = name
                out.append({**entry, "host": name})
                if (key := space_key(entry)) and space:
                    rows.append((key, space, first.get(space, now)))
            self.seen[name] = rows
        self.where = where
        for name in list(self.others):
            silent = self.unreached.get(name)
            if (silent is None and not self.seen.get(name)) or (silent is not None and now - silent >= self.drop_after):
                del self.others[name]
                self.seen.pop(name, None)
                self.unreached.pop(name, None)
        return out

    def close(self, workspace: str) -> None:
        name = self.where.get(workspace, self.name)
        self._host(name if name in self.others else self.name).close(workspace)

    def wake(self, key: str, text: str) -> bool:
        """The host of the key's newest session: the one that the record saw last, the host in force on a tie."""
        rows = [(at, name == self.name, name) for name, rows in self.seen.items() for k, _, at in rows if k == key and
                (name == self.name or name in self.others)]
        return self._host(max(rows)[2] if rows else self.name).wake(key, text)

    def paused(self) -> dict[str | None, tuple[str, str]]:
        """The stops of the host in force, then an old host's for a profile that the host in force has none of. An old host
        that does not answer gives none."""
        found = dict(self.primary.paused())
        for name, host in self.others.items():
            try:
                theirs = {} if name in self.unreached else host.paused()
            except Exception:
                continue
            for profile, stop in theirs.items():
                found.setdefault(profile, stop)
        return found

    def record(self) -> dict[str, Any]:
        """What the state file keeps (`hosts`): the host in force, each recorded session, and the old hosts with no answer."""
        return {"in_force": self.name,
                "sessions": [{"host": name, "key": key, "id": space, "seen": _stamp(at)}
                             for name, rows in self.seen.items() for key, space, at in rows],
                "unreached": {name: _stamp(at) for name, at in self.unreached.items()}}


def primary_of(host: Host) -> Host:
    """The host in force: the one that starts new sessions and whose daemon the service keeps."""
    return host.primary if isinstance(host, Hosts) else host


def read_record(path: Path) -> object:
    """The `hosts` record of the state file that the last process wrote, or None."""
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("hosts")
    except (OSError, ValueError, AttributeError):
        return None


# -- Reads: the only I/O to Linear and GitHub. A test passes its own. -----------------------------------------

FinalRead = tuple[str, float | None, tuple[str, ...]]  # (state, since, labels): `read_final`
Final = Callable[[str], FinalRead]


@dataclass(frozen=True)
class Reads:
    """The two reads of a round, each a function that raises when the read fails."""

    issues: Callable[[], list[dict[str, Any]]]
    prs: Callable[[], list[dict[str, Any]]]
    labels: Callable[[str], list[str]] | None = None  # one issue's labels: the Area of a closed issue a follow-up came from
    branch: Callable[[int], str] | None = None  # one pull request's branch: the issue of a closed pull request a follow-up came from
    final: Final | None = None  # one issue's state, when it entered it if final, and its labels


def read_linear(cfg: Config, environ: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """The team's open issues, by the module `linear.script` names (agents' `.github/linear.py`: its `list` read)."""
    return list(linear_api(cfg, environ).issues(cfg.team, list(cfg.read_states)))


def read_final(cfg: Config, key: str) -> FinalRead:
    """One issue's state, when it entered that state (seconds since the epoch) when the state is final, and its labels:
    the Linear module's `issue` and `entered`. None when no move led there (an issue made in that state). The labels come
    with the same `issue` read: a skip label keeps a canceled issue's pull request open."""
    api = linear_api(cfg)
    found = api.issue(key)
    state = str(found["state"])
    return state, (_seconds(api.entered(key, state)) if state in cfg.closed_states else None), tuple(found.get("labels") or ())


def read_branch(cfg: Config, number: int) -> str:
    """One pull request's branch name, open or closed, through `gh`."""
    done = run_quiet(["gh", "pr", "view", str(number), "--repo", cfg.repo, "--json", "headRefName", "--jq", ".headRefName"])
    if done.returncode != 0:
        raise RuntimeError(f"gh pr view {number} failed: {done.stderr.strip()[:300]}")
    return done.stdout.strip()


def read_github(cfg: Config) -> list[dict[str, Any]]:
    """The open pull requests with draft flag, checks, reviews, comments and commits, through `gh` (GH_TOKEN in the environment)."""
    # --limit 30: GitHub refuses the nested reads of more (500,000 nodes); the open pull requests are a handful
    command = ["gh", "pr", "list", "--repo", cfg.repo, "--state", "open", "--limit", "30", "--json", GH_FIELDS]
    done = subprocess.run(command, capture_output=True, text=True, timeout=GH_TIMEOUT, check=False)
    if done.returncode != 0:
        raise RuntimeError(f"gh pr list failed: {done.stderr.strip()[:300]}")
    found = json.loads(done.stdout)
    if not isinstance(found, list):
        raise RuntimeError("gh pr list did not return a list")
    return found


def real_reads(cfg: Config) -> Reads:
    """The reads of one team through its Linear module and its repository."""
    return Reads(issues=lambda: read_linear(cfg), prs=lambda: read_github(cfg),
                 labels=lambda key: list(linear_api(cfg).issue(key)["labels"]), branch=lambda number: read_branch(cfg, number),
                 final=lambda key: read_final(cfg, key))


@dataclass(frozen=True)
class Writes:
    """The writes of an escalation: a label on the pull request, a comment and a state on the issue; and the close of a
    pull request with one comment."""

    label: Callable[[int, str], None]
    comment: Callable[[str, str], None]
    move: Callable[[str, str], None]
    close: Callable[[int, str], None] | None = None  # (number, comment); None: no pull request closes
    notice: Callable[[str | None, float], None] | None = None


class MissingKey(RuntimeError):
    """A team's Linear key is not in the environment: that team's round fails (`team_round` never raises), the others go on."""


def linear_api(cfg: Config, environ: Mapping[str, str] | None = None) -> Any:
    """The `Linear` client of the module `linear.script` names, with the key of `cfg`'s team: the variable `cfg.linear_key_env`
    of the environment. The module gets a mapping with that one key, under the name it reads (LINEAR_API_KEY), so a
    call of one team never carries another team's key, and a missing key names the team and its variable, not the module's."""
    found = (environ if environ is not None else os.environ).get(cfg.linear_key_env, "").strip()
    if not found:
        raise MissingKey(f"no {cfg.linear_key_env} in the environment (the Linear key of team {cfg.team})")
    path = Path(cfg.linear_script)
    spec = importlib.util.spec_from_file_location("orchestrator_linear", path)
    if spec is None or spec.loader is None:
        raise ConfigError(f"cannot load the Linear module {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # the module's dataclasses and annotations look themselves up here
    spec.loader.exec_module(module)
    return module.Linear(module.http_post(module.api_key({DEFAULT_LINEAR_KEY_ENV: found})))


def real_writes(cfg: Config) -> Writes:
    def label(number: int, name: str) -> None:
        done = run_quiet(["gh", "pr", "edit", str(number), "--repo", cfg.repo, "--add-label", name])
        if done.returncode != 0:
            raise RuntimeError(f"gh pr edit failed: {done.stderr.strip()[:300]}")

    def close(number: int, body: str) -> None:
        # `gh pr close` deletes no branch without --delete-branch: GitHub deletes it on its own rules
        done = run_quiet(["gh", "pr", "close", str(number), "--repo", cfg.repo, "--comment", body])
        if done.returncode != 0:
            raise RuntimeError(f"gh pr close failed: {done.stderr.strip()[:300]}")

    return Writes(label=label, comment=lambda key, body: linear_api(cfg).comment(key, body),
                  move=lambda key, state: linear_api(cfg).move(key, state), close=close,
                  notice=lambda cause, at: halt_notice(cfg, linear_api(cfg), cause, at))


HALT_NOTICE = "Orchestrator setup notice."
CONFIRM_ACTION = " Inspect the launch workspace and fix host errors that prevent confirmation or cleanup."


def halt_notice(cfg: Config, api: Any, cause: str | None, at: float) -> None:
    # Read the notice from Linear so it survives a service restart and a failed write response.
    open_notice = next((i for i in api.issues(cfg.team, []) if i["state"] not in cfg.closed_states
                        and str(i.get("description") or "").startswith(HALT_NOTICE + "\n")), None)
    profile = f"Profile: `{cfg.inference_profile}`."
    body = (f"halted at {_when(at)}: {cause}\n\n{profile} Run `owlcode check` to check the setup."
            if cause else f"resumed at {_when(at)}: the setup halt cleared")
    if open_notice is None:
        if cause:
            api.create(cfg.team, f"Orchestrator halted: {cause}"[:240], f"{HALT_NOTICE}\n\n{body}",
                       ["hands-off", cfg.halt_notice_area], cfg.escalated, "high", assignee="me")
        return
    key = open_notice["identifier"]
    # Linear returns comments oldest first. A resume ends the incident even if the move to Done fails.
    lines = [*open_notice["description"].splitlines(), *(c["body"].split("\n", 1)[0] for c in open_notice["comments"])]
    history = [line for line in lines if line.startswith(("halted at ", "resumed at "))]
    last_resume = max((i for i, line in enumerate(history) if line.startswith("resumed at ")), default=-1)
    if cause:
        reported = any(line.partition(": ")[2] == cause for line in history[last_resume + 1:])
    else:
        reported = bool(history) and last_resume == len(history) - 1
    if not reported:
        api.comment(key, body)
    if cause is None:
        done = next(s["name"] for s in api.team(cfg.team)["states"] if s["type"] == "completed" and s["name"] in cfg.closed_states)
        api.move(key, done)


class Check(tuple[str, str]):
    """One line of the setup preflight, with the profiles that it belongs to. It is the pair (level, text) that a
    `Line` is, so every reader of lines goes on as before; `profiles` is None for a check that no profile owns (the service,
    Linear, GitHub, the host's list, a daemon that cannot be read): its failure halts every start. A check of some profiles
    (their credential, their models in pi, the keys that the Paseo daemon holds) fails only their cells."""

    profiles: frozenset[str] | None

    def __new__(cls, level: str, text: str, profiles: frozenset[str] | None = None) -> Check:
        self = super().__new__(cls, (level, text))
        self.profiles = profiles
        return self

    @property
    def level(self) -> str:
        return self[0]

    @property
    def text(self) -> str:
        return self[1]


def failed_checks(lines: Iterable[tuple[str, str]]) -> list[Check]:
    """The FAIL lines of a preflight, as `Check`: a plain line of two fields belongs to no profile."""
    return [c for c in (line if isinstance(line, Check) else Check(*line) for line in lines) if c.level == FAIL]


PAUSED = "paused by the owner"


def pause_path(config: Path) -> Path:
    """The owner's pause file, beside the config: `orchestrator.toml` -> `orchestrator.paused`. It is root's in the
    installed service (`install.py pause` writes it, `resume` removes it), so the agents' user cannot clear it."""
    return config.with_name(config.stem + ".paused")


@dataclass
class Halt:
    """One setup state for every team. Only the first team writes the notice. It stops every start (`scope` None), or only the
    cells of some profiles: a failed check of one profile halts that profile's cells, and the others go on."""

    cause: str | None = None
    scope: frozenset[str] | None = None  # the profiles whose cells the halt stops; None: all of them
    at: float = 0
    next_check: float = 0
    pending: bool = False
    unreported: tuple[str, float] | None = None
    narrowed: float | None = None  # when a halt last narrowed
    released: frozenset[str] = frozenset()  # the profiles that narrowing freed: only they get a fix-wait floor from `narrowed`
    reported: set[str] = field(default_factory=set)
    failed_fix: str | None = None
    retry_fix: str | None = None
    agent_key: str | None = None  # the key of a halt from an agent in error: its notice stays open for one claim wait
    probation_until: float = 0  # when that restarted start has had claim_wait_seconds, 0 outside that wait
    confirmation_error: type[Exception] | None = None
    shown: str | None = None
    check: Callable[[], Sequence[Line]] | None = None
    hold: Callable[[], bool] | None = None  # the owner's pause file exists
    owner_hold: bool = False
    daemon_hold: bool = False  # the service starts or switches Paseo's daemon: only `daemon_step` lifts it

    def pause_step(self, cfg: Config, now: float) -> str | None:
        """The owner's pause is this halt with the cause `paused by the owner`. It lasts until the file goes, not
        until a preflight passes (`recover` does nothing while it holds), and it writes no setup notice: the owner made it.
        The line of the resume, when the file went and the halt was the pause; a halt of a failed check that the pause
        replaced is entered again by the next start's preflight."""
        if self.hold is None:
            return None
        if self.hold():
            # Whatever else cleared the halt (a confirmed launch, the agent-in-error probation), a pause file that still
            # exists enters it again: the pause lasts until `resume`.
            if not self.owner_hold or self.cause is None:
                self.owner_hold = True
                self.enter(cfg, now, PAUSED)
                self.unreported = None
            return None
        if self.owner_hold:
            self.owner_hold = False
            if self.cause is not None and self.cause.startswith(PAUSED):  # also with the confirmation's suffix
                return self.resume(now, "the owner resumed")  # `pending` stays: it closes a notice that an earlier halt left open
        return None

    def blocks(self, profile: str) -> bool:
        """The halt stops the cells of this profile."""
        return self.cause is not None and (self.scope is None or profile in self.scope)

    def enter(self, cfg: Config, now: float, cause: str, profiles: frozenset[str] | None = None) -> bool:
        """Halt the cells of `profiles` (None: every start). A halt that holds already takes more profiles in (the causes join),
        and one for every start stays as it is; the same halt again keeps its times, so a start that finds the same failed check
        of another profile each round never moves the next recovery check. True when the halt is new or changed."""
        if self.cause is not None:
            if self.scope is None and profiles is not None:
                return False  # every start halts already
            if profiles is not None and self.scope is not None:
                if profiles <= self.scope and cause in self.cause:
                    return False
                joined = cause if cause.startswith(self.cause) else self.cause if cause in self.cause else f"{self.cause}; {cause}"
                profiles, cause = self.scope | profiles, joined
        self.cause, self.scope, self.at, self.next_check, self.pending = cause, profiles, now, now + 60 * cfg.halt_check_minutes, True
        if (said := str(self.text())) not in self.reported:
            self.unreported = (said, now)  # the notice names the halted profiles, as the banner does
        return True

    def enter_failed(self, cfg: Config, now: float, failed: Sequence[Check]) -> bool:
        """Halt what the failed checks stop: one that no profile owns halts every start, with its text as the cause; else the
        cells of the profiles that the failed checks own, with their texts joined. True when the halt is new or
        changed; no failed check: False."""
        if not failed:
            return False
        if any(c.profiles is None for c in failed):
            return self.enter(cfg, now, next(c.text for c in failed if c.profiles is None))
        return self.enter(cfg, now, "; ".join(dict.fromkeys(c.text for c in failed)),
                          frozenset().union(*(c.profiles for c in failed if c.profiles is not None)))

    def text(self) -> str | None:
        """The halt as the banner and the log say it: the cause, with the profiles when the halt stops only some of them."""
        if self.cause is None or self.scope is None:
            return self.cause
        return f"cells on {and_list(sorted(self.scope))} only: {self.cause}"

    def stop(self, cfg: Config, now: float, key: str, model: str, error: Exception) -> Event:
        # Command output can contain credentials. Publish only known diagnostics, never raw lines or argv.
        raw = str(error).lower()
        hints = ((r"model.*not found", "model not found"),
                 (r"credential|api key|unauthorized|keychain", "credential failure"),
                 (r"connection|daemon down", "host connection failed"), (r"timed out", "host command timed out"),
                 (r"could not confirm", "session not confirmed"))
        why = next((name for pattern, name in hints if re.search(pattern, raw)), type(error).__name__)
        cleanup = "; workspace cleanup failed" if getattr(error, "__notes__", []) else ""
        cause = f"{cfg.session_host} start failed for {key} ({model}): {why}{cleanup}"
        self.enter(cfg, now, cause)
        text = "session not confirmed" if isinstance(error, LaunchUnknown) else "no session created"
        return Event("halted", key, f"start refused for {key}: {text}", cause)

    def notify(self, write: Callable[[str | None, float], None], now: float) -> None:
        if self.unreported is not None:
            write(*self.unreported)
            self.reported.add(self.unreported[0])
            self.unreported = None
        if self.cause is None and not self.probation_until:
            self.reported.clear()
            write(None, self.at or now)
        self.pending = False

    def resume(self, now: float, reason: str) -> str:
        self.cause, self.scope, self.at, self.pending = None, None, now, True
        return f"resumed at {_when(now)}: {reason}"

    def recover(self, cfg: Config, now: float) -> str | None:
        if self.check is None or self.cause is None or self.owner_hold or self.daemon_hold or now < self.next_check:
            return None
        self.next_check = now + 60 * cfg.halt_check_minutes
        # Only a failed check that the halt stops counts: another profile's failure does not hold this halt.
        failing = [c for c in failed_checks(self.check()) if c.profiles is None or self.scope is None or c.profiles & self.scope]
        if failing:
            if all(c.profiles is not None for c in failing):
                # A profile whose check passes again leaves the halt: its cells start, the others' stay halted. A halt of every
                # start narrows to the profiles whose checks still fail once no check without a profile fails.
                still = frozenset().union(*((c.profiles & self.scope if self.scope is not None else c.profiles)
                                            for c in failing if c.profiles is not None))
                if self.scope is None or still < self.scope:
                    held = self.scope if self.scope is not None else {cfg.inference_profile, *cfg.cell_profiles}
                    self.released = frozenset(held) - still
                    self.scope, self.cause = still, "; ".join(dict.fromkeys(c.text for c in failing))
                    self.narrowed = now
                    if (said := str(self.text())) not in self.reported:
                        self.unreported, self.pending = (said, now), True
            return None
        self.retry_fix, self.failed_fix = self.failed_fix, None
        text = self.resume(now, "setup preflight passed")
        if self.agent_key is not None:
            # A passing preflight does not prove that the next agent survives: keep the notice open until its start claims,
            # or claim_wait_seconds pass without an error, so a repeat re-enters this halt with no new notice.
            self.probation_until, self.pending = now + cfg.claim_wait_seconds, False
        return text

    def claim_waited(self, issues: Sequence[Mapping[str, Any]], now: float, todo: str) -> None:
        """End the wait for the restarted agent-error start: the issue claimed, left this team's list, or
        `claim_wait_seconds` passed, so the halt may close its notice. Before that deadline, an issue that this team's list
        does not hold keeps waiting: another team's plan must not end it."""
        if not self.probation_until or self.cause is not None:
            return
        found = next((i for i in issues if str(i.get("identifier")) == self.agent_key), None)
        if (found is None or found.get("state") == todo) and now < self.probation_until:
            # Before the deadline, a team that does not hold the key cannot end the wait; at it, the wait ends anyway (R2-1).
            return
        self.probation_until, self.agent_key, self.pending, self.at = 0, None, True, now


# -- The runs and the fix rounds that no session took. ----------------------------------------------------------------


@dataclass
class Started:
    session: str
    at: float


@dataclass(frozen=True)
class StartRound:
    """What `start_decide` found: the starts and the waits in order, the keys that run, and each issue's Area."""

    items: list[Event | Wait]
    running: list[str]
    areas: dict[str, str | None]
    busy: frozenset[str] = frozenset()  # the keys of the busy sessions of the one list read
    listed: bool = True  # False: the list read failed, so `busy` knows nothing (then no pull request is stale for want of a session)


@dataclass
class Retry:
    """The newest session of a profile stopped on an API error, and the one retry that it gets: `due` is when the wake
    goes, `sent` when it went (None in a dry run or before it), and `until` the end of the window after it. A matching stop in
    that window pauses the profile; the window's end with none clears the state. `paused` is a stop that gets no retry or a second
    stop after the retry."""

    key: str
    cause: str
    due: float
    sent: float | None = None
    until: float | None = None
    cleared: bool = False
    paused: bool = False


def profile_tag(cfg: Config, profile: str | None) -> str:
    """` (NAME)` after a word of a line, only when a cell runs on another profile: with one profile in force a line
    reads as it always did. An agent with no profile label stands for every profile."""
    return f" ({profile or 'no profile label'})" if cfg.cell_profiles else ""


@dataclass
class Stops:
    """The stop and retry state that every team's Runner shares, one for each profile: the newest
    session of a profile is one for every team, so its retry is one too, and a profile's stop holds only the work that runs on
    that profile. The key `None` is a stop of a session with no profile label: it holds every profile. `events` reads the host once in each round:
    the first team's call acts, and a call with the same `token` later in the round copies the state and gives no event. The
    state is kept in memory, so a restart gives a stop that still sits in a pause one more retry."""

    pause: dict[str | None, tuple[str, str]] = field(default_factory=dict)  # profile -> (key, cause) of the blocking state, a retry's wait or a pause
    retry: dict[str | None, Retry] = field(default_factory=dict)  # profile -> the stop and its one retry
    resumed: dict[str | None, float] = field(default_factory=dict)  # profile -> when its last pause ended: `fix_events` counts a fix wait from then
    resumed_at: float | None = None  # when a halt ended (the setup preflight passed, or the host confirmed a session): the floor of every profile's fix waits
    wake_said: set[tuple[str, float, str]] = field(default_factory=set)  # (key, due time, error type) of a retry_wake that failed
    round: object | None = None  # the round whose stop `events` already read: another team's call then copies it

    def blocks(self, profile: str) -> bool:
        """The work of `profile` waits: its own stop holds (a retry's wait or a pause), or a stop with no profile label does."""
        return profile in self.pause or None in self.pause

    def floor(self, profile: str) -> float | None:
        """The end of the newest pause or halt that held `profile`'s fix waits: they count from then."""
        return max((t for t in (self.resumed_at, self.resumed.get(profile), self.resumed.get(None)) if t is not None), default=None)

    @staticmethod
    def retry_allowed(cfg: Config, cause: str) -> bool:
        """Whether a stop with this cause gets one retry: the third field of its `stop_patterns` pair, false for a
        cause that a retry cannot clear (a usage limit that resets in hours, an empty balance, a sign-in that the owner renews).
        A cause that no pair names gets the retry: the host reports only a cause of a pair. The pairs of every harness that a
        cell runs on count."""
        return next((retry for stops in harness_stops(cfg).values() for _, name, retry in stops if name == cause), True)

    @staticmethod
    def paused_event(cfg: Config, profile: str | None, key: str, cause: str) -> Event:
        """The line of a pause: a cause with no retry, or a stop that the retry did not clear. With cells on
        other profiles it names the profile, and the work that it holds."""
        work = "start, wake or escalation" + (" of a cell on that profile" if cfg.cell_profiles and profile else "")
        return Event("paused", key, f"paused{profile_tag(cfg, profile)}: {cause} (the newest session, of {key}, stopped on an API error): "
                     f"no {work} until the owner sends to that agent or archives it", cause)

    def events(self, host: Host, cfg: Config, now: float, dry: bool, token: object | None = None) -> list[Event]:
        """Read the host's stops into `pause` and give each profile its one retry. While the newest session
        of a profile (of the orchestrator's label) stopped on an API error that matches `stop_patterns`, the round starts no
        session on that profile, from Todo or for a fix, sends no wake to a session of it and escalates no fix wait of it
        (`Runner.blocked`); the work of the other profiles goes on. A cause that may clear gets one wake, `Retry`, after
        `retry_wait_minutes` (`retrying`, with the cause); a matching stop in the window after it pauses the profile (`paused`),
        and so does a cause whose pair says that no retry helps. A stop that clears ends the profile's state (`resumed`, which
        sets its `resumed` time): before the retry at once, after it at the end of the window. A wake that the session cannot
        take (a process that ended) pauses too. The owner can end it: a send to the stopped agent (it is then running or
        idle), or its archive (it leaves `paseo ls`; the pause goes on when the next newest agent of the profile stopped too).
        `close_events` runs during it and archives the stopped agent 12 h after its issue reached Done, Canceled or Duplicate.
        A dry run sends nothing and writes no state. A host that fails to answer keeps the states as they were: the round's own
        list read then says why. Every team shares this object, so one round reads the stops once: a later call with the same
        `token` gives no event and no send. The state is kept in memory, so a restart gives a stop that still sits in a pause
        one more retry."""
        if token is not None and token is self.round:
            return []
        if token is not None:
            self.round = token
        try:
            found = host.paused()
        except Exception:
            return []  # a host that cannot answer keeps the pauses and the retries as they were
        events: list[Event] = []
        for profile in dict.fromkeys([*found, *self.retry]):
            events += self.profile_events(host, cfg, now, dry, profile, found.get(profile))
        return events

    def profile_events(self, host: Host, cfg: Config, now: float, dry: bool, profile: str | None,
                       found: tuple[str, str] | None) -> list[Event]:
        """One profile's stop: `events` for it, with `found` the host's stop of that profile (None: its newest session did not stop)."""
        before = self.retry.get(profile)
        if found is None:
            if before is not None and before.sent is not None and now < (before.until or before.sent):
                # The retry went and the stop cleared: wait out its window, so a quick second stop still pauses.
                before.cleared = True
                self.pause[profile] = (before.key, before.cause)
                return []
            self.pause.pop(profile, None)
            self.retry.pop(profile, None)
            if before is None:
                return []
            self.resumed[profile] = now
            return [Event("resumed", before.key, f"resumed{profile_tag(cfg, profile)}: the newest session no longer shows {before.cause}",
                          before.cause)]
        key, cause = found
        state = before
        if state is not None and state.key == key and state.paused:
            self.pause[profile] = found
            if state.cause == cause:
                return []  # the pause goes on, with no second event
            self.retry[profile] = Retry(key, cause, now, paused=True)
            return [self.paused_event(cfg, profile, key, cause)]
        if state is None or state.key != key or (state.sent is None and state.cause != cause):
            # A stop that this run has not retried yet. A cause whose pair says no retry pauses at once.
            self.pause[profile] = found
            if not self.retry_allowed(cfg, cause):
                self.retry[profile] = Retry(key, cause, now, paused=True)
                return [self.paused_event(cfg, profile, key, cause)]
            self.retry[profile] = Retry(key, cause, now + 60 * cfg.retry_wait_minutes)
            return [Event("retrying", key, f"retrying {key}{' on ' + (profile or 'no profile label') if cfg.cell_profiles else ''} after an API error ({cause})", cause)]
        self.pause[profile] = found
        if state.sent is None:
            # The retry waits. At its time one wake goes to the stopped session; a failed send is tried again.
            if now < state.due or dry:
                return []
            try:
                sent = host.wake(key, RETRY_TEXT)
            except Exception as e:
                if (key, state.due, type(e).__name__) not in self.wake_said:
                    self.wake_said.add((key, state.due, type(e).__name__))
                    return [Event("wake_failed", key, f"retry of the session of {key} failed: {type(e).__name__}: {e}",
                                  type(e).__name__)]
                return []
            if not sent:
                # The session cannot take the retry (its process ended, or the daemon lost it): the stop stays, as a pause.
                state.paused = True
                return [self.paused_event(cfg, profile, key, cause)]
            state.sent, state.until = now, now + 60 * cfg.retry_wait_minutes
            return []
        # The retry went: a stop after it pauses, and so does the same stop still there at the window's end.
        if state.cause != cause or state.cleared or now >= (state.until or state.sent):
            state.cause, state.paused = cause, True
            return [self.paused_event(cfg, profile, key, cause)]
        return []


@dataclass
class Runner:
    """The runs this process started from Todo (`start_round`). The table is lost at a restart: then the run's own claim
    (the move to In Progress) and the busy sessions in the host's list are the guards against a second start."""

    cfg: Config
    host: Host
    writes: Writes | None = None
    clock: Callable[[], float] = time.time
    live: dict[str, Started] = field(default_factory=dict)
    last_start: dict[str, float] = field(default_factory=dict)
    tries: dict[str, int] = field(default_factory=dict)  # starts of a key that Linear still shows in Todo
    fix_starts: dict[str, float] = field(default_factory=dict)  # key -> when a session was started for its fix
    unwritten: dict[str, str] = field(default_factory=dict)  # key -> the run line of a start whose write failed: each round
    # writes it again while the issue is in Todo, because `.github/linear.py follow_up` reads a start only from it
    halt: Halt = field(default_factory=Halt)
    confirm_launch: Callable[[], list[Event]] | None = None
    confirmed_slot: str | None = None  # reserve the slot until the next successful plan read
    woke: dict[str, float] = field(default_factory=dict)  # key -> the wait's start (`waiting_since`) whose session was woken: one wake
    # for each event, in memory only (a restart may wake once more: the draft claim still guards the fix round)
    todo_wake: dict[str, bool] = field(default_factory=dict)  # key in Todo -> True: its idle session was woken, False: the wake found none
    wake_said: set[tuple[str, float, str]] = field(default_factory=set)  # (key, wait's start, error type) of a wake_failed already printed
    shown: dict[str, Any] | None = None  # the state the loop printed last (`shown_state`); None after a failed round
    said_at: float | None = None  # when the loop printed last: a round or a heartbeat
    since: str = ""  # the time of the last round that printed
    last: dict[str, Any] | None = None  # the last round's plan: a failed round writes it again, with the failure
    queue: PinnedComment | None = None  # the pinned queue, None with no `queue_issue`
    finals: dict[str, tuple[str, float | None, float, tuple[str, ...]]] = field(default_factory=dict)  # key -> (state, since,
    # read at, labels): for the closes of the workspaces and of the canceled issues' pull requests
    failed: dict[tuple[str, str], tuple[float, str]] = field(default_factory=dict)  # (step, key) -> (time of the last try, the
    # error's type): a read or a close that failed is tried again after an hour, and a repeated error prints once
    told: set[str] = field(default_factory=set)  # the workspaces a dry run printed `would close` for: once each
    told_prs: set[int] = field(default_factory=set)  # the pull requests a dry run printed `would close PR` for: once each
    by_hand: list[Dropped] = field(default_factory=list)  # this round's pull requests of canceled issues that are the owner's
    elsewhere: Callable[[], int] = field(default=lambda: 0)  # the slots that the other teams' runs hold: `max_runs` is one number for all teams
    host_name: str = field(default_factory=socket.gethostname)  # the second half of each start token
    own_tokens: set[str] = field(default_factory=set)  # the start tokens of this process: its own run line never blocks its retry
    prestart: Callable[[], Sequence[Line]] = field(default=lambda: [])  # the setup preflight before a start
    stops: Stops = field(default_factory=Stops)  # the stop and its one retry, shared by every team's Runner

    @property
    def resumed_at(self) -> float | None:
        """When a halt ended: the floor of every profile's fix waits (a pause's end is per profile: `Stops.floor`)."""
        return self.stops.resumed_at

    @resumed_at.setter
    def resumed_at(self, value: float | None) -> None:
        self.stops.resumed_at = value

    def profile_of(self, issue: Mapping[str, Any]) -> str:
        """The profile that the issue's work runs on: its cell's, from its difficulty labels as they are now. A
        start from Todo, a fix start, and the wake or the escalation of a session that ran on the issue's cell all use it."""
        return run_settings(issue.get("labels") or [], self.cfg)[2]

    def paused(self, profile: str) -> bool:
        """The work of `profile` waits for a stop: its newest session's, or a session's with no profile label. With no cell on
        another profile, any stop holds all of it (a stop of a profile that is no longer in force too)."""
        return self.stops.blocks(profile) or (not self.cfg.cell_profiles and bool(self.stops.pause))

    def halted(self, profile: str) -> bool:
        """A failed setup check stops the cells of `profile`: the halt's scope, or every cell with no cell on another profile."""
        return self.halt.blocks(profile) or (not self.cfg.cell_profiles and self.halt.cause is not None)

    def blocked(self, profile: str) -> str:
        """Why the work of `profile` waits: `paused`, `halted`, or an empty text."""
        return "paused" if self.paused(profile) else "halted" if self.halted(profile) else ""

    def everything_blocked(self) -> bool:
        """Every profile waits, or the cells run on one profile only: a start is then held with no line for each issue
        (a mix lists the issues that wait: `held_waits`)."""
        if None in self.stops.pause or (self.halt.cause is not None and self.halt.scope is None):
            return True
        return not self.cfg.cell_profiles and (bool(self.stops.pause) or self.halt.cause is not None)

    def start_run(self, key: str, mode: str, model: str = "", effort: str = "", fix: tuple[int, str] | None = None,
                  profile: str = "") -> str:
        """Start a session for the key on the cell's profile (empty: the profile in force), and return the start's
        token. The token reaches the session only through `{token}` in the run's command (`uses`): the run line carries it
        only then (`token_line`)."""
        label, cwd = session_label(key, self.tries.get(key, 0) + 1), str(Path(self.cfg.run_cwd).expanduser())
        token = start_token(self.clock(), self.host_name)
        command = format_run(self.cfg, key, mode, model, effort, label, cwd, token, profile)
        def remember(session: str) -> None:
            self.last_start[key] = self.clock()
            self.own_tokens.add(token)
            self.live[key] = Started(session, self.last_start[key])

        try:
            remember(self.host.start(label, command, cwd))
        except LaunchUnknown as e:
            probe = e.confirm

            def confirm() -> list[Event]:
                session = probe()
                self.confirm_launch = None
                if session is None:
                    return []
                remember(session)
                return self.record_start(key, model, effort, token, fix, profile)

            self.confirm_launch = confirm
            raise
        return token

    def record_start(self, key: str, model: str, effort: str, token: str, fix: tuple[int, str] | None = None,
                     profile: str = "") -> list[Event]:
        profile = profile or self.cfg.inference_profile
        if fix is None:
            self.tries[key] = self.tries.get(key, 0) + 1
            event = Event("started", key, f"started {key} ({model}, {effort})", model=model, effort=effort)
            line = self.token_line(model, effort, token, profile)
        else:
            self.fix_starts[key] = self.clock()
            number, why = fix
            event = Event("started_fix", key, f"started a fix session for {key} ({model}, {effort})", why, model, effort)
            line = fix_run_line(model, effort, self.cfg.review, number, profile, self.workers_given(profile, effort))
        if self.writes is not None:
            try:
                self.writes.comment(key, line)
            except Exception as e:
                if fix is None:
                    self.unwritten[key] = line
                return [event, Event("run_line_failed", key, f"run line of {key} failed: {type(e).__name__}", type(e).__name__)]
        return [event]

    def token_line(self, model: str, effort: str, token: str, profile: str = "") -> str:
        """The run line of a start from Todo: with its token when the start prompt gave the token to the session, else
        without one. A token that the session never got would make the session take its own start for another's.
        The line names the cell's profile and the review of the profile in force."""
        profile = profile or self.cfg.inference_profile
        given = uses(cell_config(self.cfg, profile, effort), "token")
        return run_line(model, effort, self.cfg.review, token if given else "", profile, self.workers_given(profile, effort))

    def workers_given(self, profile: str, effort: str) -> int:
        """The share of pytest workers that the start gave the session (`pytest_workers`), 0 when the cell's command has no place for it."""
        cell = cell_config(self.cfg, profile or self.cfg.inference_profile, effort)
        return pytest_workers(cell.max_runs, cell.endpoint.cpus) if uses(cell, "workers_env") or uses(cell, "workers_flag") else 0

    def setup_failure_events(self, issues: Sequence[Mapping[str, Any]]) -> list[Event]:
        """A start whose Paseo agent is in `error` before any claim is a setup failure: the halt stops the
        starts, the agent is archived, and its try is not counted (AGENTS.md: a setup failure does not count as a try).
        The halt resumes when the preflight passes, and only then does the issue start again. A key that claimed or left
        Todo in the meantime, or a start older than `claim_wait_seconds`, is not a setup failure: its agent's error belongs
        to that run. A list that fails says nothing: the next round reads again."""
        now = self.clock()
        todo = {str(i["identifier"]): i for i in issues if i.get("state") == self.cfg.todo}
        events: list[Event] = []
        for key, started in list(self.live.items()):
            issue = todo.get(key)
            if issue is None or now - started.at >= self.cfg.claim_wait_seconds:
                continue
            try:
                failed = self.host.failed(started.session)
            except Exception:
                failed = False
            if not failed:
                continue
            del self.live[key]
            self.tries.pop(key, None)  # no try is counted
            try:
                self.host.close(started.session)  # an archive keeps the agent's history for the owner to read
            except Exception:
                pass  # the halt and its notice matter more than the archive
            model, *_ = run_settings(issue.get("labels") or [], self.cfg)
            cause = (f"{self.cfg.session_host} start failed for {key} ({model}): the agent stopped in error before its claim")
            self.halt.enter(self.cfg, now, cause)
            self.halt.agent_key = key  # its notice stays open until the restarted start claims
            events.append(Event("halted", key, f"start failed for {key}: the agent is in error before its claim", cause))
        return events

    def fix_round(self, issues: Sequence[Mapping[str, Any]], prs: Sequence[Mapping[str, Any]], dry: bool) -> list[str]:
        return [e.text for e in self.fix_events(issues, prs, dry)]

    def pause_plan(self) -> list[dict[str, Any]]:
        """The plan's `pause`: one row for each profile with a stop: the stopped session's key and cause, and whether the row
        says `retrying` or `paused`. With cells on other profiles the row has the profile too (None: a session with
        no profile label)."""
        return [{**({"profile": profile} if self.cfg.cell_profiles else {}), "key": key, "cause": cause,
                 "phase": "paused" if (state := self.stops.retry.get(profile)) is not None and state.paused else "retrying"}
                for profile, (key, cause) in self.stops.pause.items()]

    def pause_events(self, dry: bool = False, token: object | None = None) -> list[Event]:
        """The shared stops' events for this round (`Stops.events`): each profile's one retry and pause, read once for every
        team. A setup failure archives its agent within the claim wait too."""
        return self.stops.events(self.host, self.cfg, self.clock(), dry, token)

    def fix_began(self, issue: Mapping[str, Any]) -> float | None:
        """When a session was started for this issue's fix and no escalation followed: the issue's run line `fix round`
        (`fix_started_at`, which survives a restart) or this process's own memory of it (a failed run-line write)."""
        times = [t for t in (fix_started_at(issue), self.fix_starts.get(str(issue["identifier"]))) if t is not None]
        return max(times, default=None)

    def sessions_read(self) -> tuple[set[str], set[str]] | None:
        """One read of the host's list: the keys that have a session which may still claim a fix (the busy sessions of the
        orchestrator's label, and the sessions this process started that run and that the list does not show: a plain process
        has no status), then the keys that have a finished session in the list (idle or done). None when the list cannot be
        read. A session that the list shows idle or done has finished its run, however long its process stays open (an
        interactive session does): it claims nothing."""
        try:
            listed = [(k, str(s.get("agent_status") or "")) for s in self.host.workspaces() if (k := own_key(s.get("label")))]
        except Exception:
            return None
        shown = {k for k, _ in listed}
        beside = {k for k, status in listed if status in BUSY} | {k for k in self.live if k not in shown}
        return beside, {k for k, status in listed if status in FINISHED} - beside

    def wake_events(self, key: str, pr: Mapping[str, Any], why: str, since: float, dry: bool,
                    sessions: Callable[[], tuple[set[str], set[str]] | None]) -> list[Event]:
        """Wake the session that opened the pull request, once for each event. `since` is the event's time, kept in
        `woke` after a send. A busy session gets no message, and nor does a round with no readable list: the next round
        tries again, until the session is idle or the wait ends. A host that finds no session or a busy one answers False: no
        event, and the ladder goes on. A host that fails raises: one `wake_failed` for each wait and error type. A dry run
        sends nothing and says `would wake` for a key whose session the list shows finished. No Linear comment. `fix_events`
        calls it only while the event is younger than `claim_wait_seconds`."""
        found = sessions()
        if found is None or key in found[0]:
            return []
        if dry:
            if key not in found[1]:
                return []
            self.woke[key] = since
            return [Event("would_wake", key, f"would wake the session of {key} ({why})", why)]
        try:
            sent = self.host.wake(key, wake_text(int(pr["number"]), why))
        except Exception as e:
            if (key, since, type(e).__name__) in self.wake_said:
                return []
            self.wake_said.add((key, since, type(e).__name__))
            return [Event("wake_failed", key, f"wake of the session of {key} failed: {type(e).__name__}: {e}", type(e).__name__)]
        if not sent:
            return []
        self.woke[key] = since
        return [Event("woke", key, f"woke the session of {key} ({why})", why)]

    def fix_events(self, issues: Sequence[Mapping[str, Any]], prs: Sequence[Mapping[str, Any]], dry: bool) -> list[Event]:
        """A fix that no session took: the issue waits for a fix (`can_resume`, not the restart label) and its pull request is
        still ready `claim_wait_seconds` after it began to wait. The session that opened the pull request runs its own fix round
        and claims it with the draft mark. At once, when the round sees the event, the orchestrator wakes that session with a
        message, if the host shows it idle (`wake_events`). When none claimed, the orchestrator starts one new session for the issue, once (the owner's
        call, 2026-11-16: `fix_began` keeps the start, so a restart or a later round starts no second one), within
        `max_runs` and never beside a busy session of the key in the host's list (or one that this process started and the list does not show). It escalates when that session
        took the fix neither within a second `claim_wait_seconds`, or when the pull request waits again after it. A wait that
        could not start a session (a session beside it, no free slot, no list) escalates after two `claim_wait_seconds`.
        The times come from GitHub, so a restart loses nothing, except the pause floor: `resumed_at` is kept in memory, so a restart
        within `claim_wait_seconds` after a resume counts from GitHub's times again. While the host's pause of a profile holds
        (`Stops.pause`) the round wakes, starts and escalates nothing for an issue whose cell runs on that profile (a pause of a session with no profile label holds every issue): after the pause, a wait counts from the pause's end
        (`Stops.floor`: the session that the owner just sent to is working on the fix). A wait for failed gates does
        nothing while a rerun can still come (`rerun_pending`)."""
        lines: list[Event] = []
        now, wait = self.clock(), self.cfg.claim_wait_seconds
        for key, started in list(self.live.items()):
            if not self.host.alive(started.session):
                del self.live[key]
        seen: list[tuple[set[str], set[str]] | None] = []  # one read of the host's list for the round

        def sessions() -> tuple[set[str], set[str]] | None:
            if not seen:
                seen.append(self.sessions_read())
            return seen[0]
        planned: set[str] = set()  # the keys started in this round: each holds a slot
        for issue in sorted(issues, key=_order):
            key = str(issue["identifier"])
            pr = pr_for(key, prs)
            why = can_resume(issue, pr, self.cfg)
            if not why or why == "restart label" or pr is None or pr.get("isDraft"):
                continue
            profile = self.profile_of(issue)
            if self.paused(profile):
                continue  # the profile's session stopped on an API error: its wake, start and escalation wait for the pause's end
            since = waiting_since(pr, why, self.cfg)
            if since is None:
                continue
            if rerun_pending(pr, why, since, now, self.cfg):
                continue  # a rerun of the gates can still come: no wake, no start, no escalation
            began = self.fix_began(issue)
            event_at = since
            retry_fix = self.halt.retry_fix == key and began is None
            floor = self.stops.floor(profile)
            if floor is not None and not retry_fix:
                if began is not None and began >= since:
                    began = max(began, floor)
                since = max(since, floor)
            if now - since < wait or self.halted(profile):
                # A halt stops new sessions and escalations, not wakes of an existing session.
                if (began is None or began < since) and self.woke.get(key) != event_at:
                    lines += self.wake_events(key, pr, why, event_at, dry, sessions)
                continue
            reason = ""  # why no session was started for this wait
            if began is None and not escalated_since(issue, event_at):  # an escalation that failed to move the issue is tried again, not started
                found = sessions()
                beside = found[0] if found is not None else None
                recent = now - self.last_start.get(key, -math.inf) < self.cfg.claim_grace_seconds
                if beside is None:
                    reason = "the session list could not be read"
                elif key in beside or recent:
                    reason = "a session of the key was still busy"
                elif len((self.used(issues, prs, beside) - {key}) | planned) + self.elsewhere() >= self.cfg.max_runs:  # its new session takes the slot of its old one
                    reason = "no slot was free"
                else:
                    planned.add(key)
                    lines += self.start_fix(issue, pr, why, dry)
                    continue
                if now - since < 2 * wait:
                    continue  # it waits: the session beside it may still claim, and a slot may free
            elif began is not None and began >= since:
                if now - began < wait:
                    continue  # the session that was started has its time
            if retry_fix:
                continue  # allow the failed start to retry, but do not shorten the time before escalation
            minutes = int((now - (began if began is not None and began >= since else since)) // 60)
            if began is None:
                said = f"{why}: no session took the fix in {minutes} min"
            elif began >= since:
                said = f"{why}: the session started for the fix did not take it in {minutes} min"
            else:
                said = f"{why}: waits again after the session started for the fix, and no session took it in {minutes} min"
            if dry or self.writes is None:
                lines.append(Event("would_escalate", key, f"would escalate {key} ({said})", why))
                continue
            if began is not None:
                body = (f"{ESCALATION} ({why}), and no session took it, though the orchestrator started one session for the fix "
                        f"({'it did not claim it' if began >= since else 'the pull request waits again after its fix'}): "
                        f"{minutes} minutes later the pull request is still ready for review, not in draft. "
                        f"Move the issue to Todo: the orchestrator then starts a session for it with its run settings, and can wake that session later.")
            else:
                body = (f"{ESCALATION} ({why}), and no session took it: {minutes} minutes later the pull request "
                        f"is still ready for review, not in draft. The orchestrator started no session for it ({reason}). "
                        f"Move the issue to Todo: the orchestrator then starts a session for it with its run settings, and can wake that session later.")
            # Label, comment, move. A failed write leaves the rest for the next round: the label is idempotent, and the
            # comment is written once per event (`escalated_since`). One issue's failure never stops the others.
            try:
                self.writes.label(int(pr["number"]), self.cfg.escalated_label)
                if not escalated_since(issue, event_at):
                    self.writes.comment(key, body)
                self.writes.move(key, self.cfg.escalated)
            except Exception as e:  # the next round tries again; the other issues and the plan go on
                lines.append(Event("escalation_failed", key, f"escalation of {key} failed: {type(e).__name__}: {e}", type(e).__name__))
                continue
            self.fix_starts.pop(key, None)  # the escalation ends the cycle of that start
            lines.append(Event("escalated", key, f"escalated {key} ({said})", why))
        return lines

    def start_fix(self, issue: Mapping[str, Any], pr: Mapping[str, Any], why: str, dry: bool) -> list[Event]:
        """Start the one session for a fix (`fix_events`) and write its run line, which keeps the start across a restart. A dry
        run says it would, and starts nothing. A fix start runs the setup preflight too: a setup that changed under
        the running service enters the halt instead of spending a session on an agent that stops at once."""
        key = str(issue["identifier"])
        model, effort, profile = run_settings(issue.get("labels") or [], self.cfg)
        if dry:
            return [Event("would_start_fix", key, f"would start a fix session for {key}", why, model, effort)]
        if setup := self.setup_halt(profile)[0]:
            self.halt.failed_fix = key  # the fix retries in the resume round, as a start that raised does
            return [Event("halted", key, "no fix start this round: the setup preflight failed", setup)]
        fix = (int(pr["number"]), why)
        try:
            token = self.start_run(key, "start", model, effort, fix, profile)
        except Exception as e:
            self.halt.failed_fix = key
            return [self.halt.stop(self.cfg, self.clock(), key, model, e)]
        return self.record_start(key, model, effort, token, fix, profile)

    def setup_halt(self, profile: str | None = None) -> tuple[str, bool]:
        """Run the setup preflight (`prestart`) and halt what each failed check stops: a check that no profile owns
        halts every start, a check of some profiles halts their cells. The text of the first failed check that stops `profile`
        (None: any start), else an empty text: a start of a cell on another profile goes on while its profile's checks pass.
        Then whether the halt is new or changed: the same failed check found again says nothing again."""
        failed = failed_checks(self.prestart())
        changed = self.halt.enter_failed(self.cfg, self.clock(), failed)
        return next((c.text for c in failed if c.profiles is None or profile is None or profile in c.profiles), ""), changed

    def used(self, issues: Sequence[Mapping[str, Any]], prs: Sequence[Mapping[str, Any]],
             busy: Collection[str] = ()) -> set[str]:
        """The slots in use (`building`, the only count that `max_runs` limits): the keys of `held`."""
        return set(self.held(issues, prs, busy))

    def held(self, issues: Sequence[Mapping[str, Any]], prs: Sequence[Mapping[str, Any]],
             busy: Collection[str] = ()) -> dict[str, str]:
        """Each slot in use, key -> why it counts: each issue in In Progress, as the state's name, whatever its pull request
        and its session show. A fix round counts too: its pull request still says "changes requested", and its session
        is at rest between two turns or before the wake, so any signal of the pull request or the session would free its slot
        for a moment, and a new start would take it (`build 5/3`, 2026-11-24). Then each key this process started less than
        `claim_grace_seconds` ago whose issue is still in Todo (its claim may not show yet): `started N min ago, not claimed
        yet`. Then each key of `busy` (the busy sessions of the round's one list read) whose issue is in Todo: `session busy,
        not claimed yet`; a restart loses `last_start`, and without this a busy session of a Todo issue would hold no slot. A
        run in review holds no slot, whoever started it: its issue is not In Progress, and its session only waits for the
        verdict. The count can pass `max_runs` by a review that sends a pull request back while all slots are full: the wake of
        a fix round's session waits for no slot. A new session for a fix still needs a free slot (`fix_events`: it counts the
        other In Progress issues, not its own). Keys and reasons only: no issue text."""
        now = self.clock()
        todo = {str(i["identifier"]) for i in issues if i.get("state") == self.cfg.todo}
        slots = {str(i["identifier"]): self.cfg.in_progress for i in issues if i.get("state") == self.cfg.in_progress}
        for key, at in self.last_start.items():
            if now - at < self.cfg.claim_grace_seconds and key in todo:
                slots[key] = f"started {max(1, math.ceil((now - at) / 60))} min ago, not claimed yet"
        for key in sorted(todo & set(busy)):
            slots.setdefault(key, "session busy, not claimed yet")
        return slots

    def origin_areas(self, issues: Sequence[Mapping[str, Any]], prs: Sequence[Mapping[str, Any]],
                     labels: Callable[[str], list[str]] | None, branch: Callable[[int], str] | None
                     ) -> tuple[dict[str, str | None], dict[str, str]]:
        """The Area of each origin that the issues name, by the issues read, then one read for a closed issue or pull
        request. A failed read is kept apart (origin -> reason): the issues that name it wait, and the round goes on."""
        known: dict[str, str | None] = {str(i["identifier"]): own_area(i.get("labels") or [], self.cfg) for i in issues}
        failed: dict[str, str] = {}
        for issue in issues:
            origin = origin_key(issue)
            if own_area(issue.get("labels") or [], self.cfg) is not None or not origin or origin in known or origin in failed:
                continue
            try:
                key: str | None = origin
                if origin.startswith("#"):
                    number = int(origin[1:])
                    open_pr = next((p for p in prs if int(p.get("number") or 0) == number), None)
                    name = str(open_pr["headRefName"]) if open_pr else (branch(number) if branch else "")
                    key = key_in(name)
                    if key in known:
                        known[origin] = known[key]
                        continue
                known[origin] = own_area(labels(key), self.cfg) if key and labels else None
            except Exception as e:  # one origin that cannot be read never fails the round
                failed[origin] = f"{type(e).__name__}: {e}"
        return known, failed

    def start_round(self, issues: Sequence[Mapping[str, Any]], prs: Sequence[Mapping[str, Any]], dry: bool,
                    labels: Callable[[str], list[str]] | None = None, branch: Callable[[int], str] | None = None) -> list[str]:
        return [item.text for item in self.start_decide(issues, prs, dry, labels, branch).items]

    def start_decide(self, issues: Sequence[Mapping[str, Any]], prs: Sequence[Mapping[str, Any]], dry: bool,
                     labels: Callable[[str], list[str]] | None = None, branch: Callable[[int], str] | None = None) -> StartRound:
        """Start the issues that `pick_starts` picks, within `max_runs`. An issue runs when it is In Progress, or in
        review with its pull request open (agents/README.md "What can start now"), or when this process started it, unless
        it is in Escalated: an interactive session stays alive after its run ends, and an issue that waits for the owner holds
        no Area. A key started less than `claim_grace_seconds` ago runs too: Linear may not show its claim yet. The implementer's own
        claim, the move to In Progress, is the guard against a second start; the table of starts is lost at a restart, so
        the session host's list is the second guard: a key whose session carries the orchestrator's label and is
        busy runs too, whoever started it. One read for the round. A failed read starts nothing this round and gives one
        event: a start with no list could be the duplicate. While the host's pause of a profile holds (`Stops.pause`)
        no cell on that profile starts: the session would stop on the same error. The same holds for a halt of that profile:
        the issue waits (`its cell runs on NAME, which is paused`), and the cells of the other profiles start.
        With cells on one profile only, or after a stop with no profile label or a halt of every start, nothing starts and no
        issue gets a line, as before. The third guard is the run line of another starter: an issue
        in Todo whose newest run line carries a start token that this process did not make, younger than
        `started_elsewhere_seconds`, waits (`started elsewhere`). After a restart this process's own tokens are lost, so its
        own unclaimed start waits too, for that time at most. Just before the round's real starts, the setup preflight runs
        once (`prestart`): a FAIL enters the halt, so a setup that changed under the running service costs no start, and a
        round with no start to make calls no preflight. A failed check of one profile halts only its cells
        (`setup_halt`).""" 
        lines: list[Event | Wait] = [] if dry else self.write_again(issues)
        now = self.clock()
        alive = {k for k, started in self.live.items() if self.host.alive(started.session)}
        recent = {k for k, at in self.last_start.items() if now - at < self.cfg.claim_grace_seconds} | alive
        busy: set[str] = set()
        idle: set[str] = set()
        no_list = False
        try:
            spaces = [(k, str(s.get("agent_status") or "")) for s in self.host.workspaces() if (k := own_key(s.get("label")))]
            busy = {k for k, status in spaces if status in BUSY}
            idle = {k for k, status in spaces if status == "idle"} - busy
            recent |= busy
        except Exception as e:
            no_list = True
            lines.append(Event("start_failed", "", f"no start this round: the session list failed: {type(e).__name__}", type(e).__name__))
        running = [i for i in issues if i.get("state") == self.cfg.in_progress
                   or (str(i["identifier"]) in recent and i.get("state") != self.cfg.escalated)
                   or (i.get("state") in self.cfg.review_states and pr_for(str(i["identifier"]), prs) is not None)]
        # An issue in Escalated holds no Area: it waits for the owner, often overnight. Its pull request still holds its
        # related links.
        waiting = [i for i in issues if i.get("state") == self.cfg.escalated and i not in running
                   and pr_for(str(i["identifier"]), prs) is not None]
        for key in [k for k in self.tries if all(str(i["identifier"]) != k or i.get("state") != self.cfg.todo
                                                 or self.claimed_since_start(i) for i in issues)]:
            del self.tries[key]  # claimed (or gone): its starts no longer count
        # A start whose session ended before its claim (a locked keychain, a wrong command) is tried once more, then the
        # key waits for the owner: each start opens a new workspace, and the owner reads the last one.
        stuck = sorted(k for k, n in self.tries.items() if n >= 2 and k not in recent)
        lines += [Wait(k, "started twice and never claimed: read its last session; to start it again, run "
                          "`owlcode restart` or move it out of Todo and back") for k in stuck]
        candidates = [i for i in issues if can_start(i, self.cfg) and str(i["identifier"]) not in recent | set(stuck)]
        in_todo = {str(i["identifier"]) for i in issues if i.get("state") == self.cfg.todo}
        for key in [k for k in self.todo_wake if k not in in_todo]:
            del self.todo_wake[key]
        # A follow-up waits for the merge of its pull request: main does not hold the change before it.
        unmerged = {str(i["identifier"]): n for i in candidates if (n := unmerged_origin(i, prs)) is not None}
        lines += [Wait(key, merge_wait(n)) for key, n in unmerged.items()]
        candidates = [i for i in candidates if str(i["identifier"]) not in unmerged]
        # Another orchestrator started the issue a moment ago and its session has not claimed it yet: its run line
        # carries a token that this process did not make. The issue holds its Area and its related links, like one that runs.
        # A run line whose write failed is no sign: such a start has no guard.
        elsewhere: dict[str, float] = {}
        for issue in candidates:
            found = pending_start(issue, now, self.cfg.started_elsewhere_seconds)
            if found is not None and found[0] not in self.own_tokens:
                elsewhere[str(issue["identifier"])] = found[1]
        # The start's time, not its age: the reason stays the same from round to round, so the loop prints no new plan for it.
        lines += [Wait(k, f"started elsewhere at {datetime.fromtimestamp(at, UTC):%H:%M} UTC, not claimed yet") for k, at in elsewhere.items()]
        others = [i for i in candidates if str(i["identifier"]) in elsewhere]
        candidates = [i for i in candidates if str(i["identifier"]) not in elsewhere]
        if no_list or self.everything_blocked():
            candidates = []
        else:
            candidates = self.hold_back(candidates, lines)
        known, failed = self.origin_areas(issues, prs, labels, branch)

        def area(issue: Mapping[str, Any]) -> str | None:
            return area_of(issue, self.cfg, known)

        unread = {str(i["identifier"]): failed[o] for i in candidates if (o := origin_key(i)) in failed}
        free = self.cfg.max_runs - len(self.used(issues, prs, busy)) - self.elsewhere()
        by_key = {str(i["identifier"]): i for i in candidates}
        picks = pick_starts(candidates, [*running, *others], free, self.cfg.distinct_areas, area, waiting)
        # Only a real start runs the setup preflight, once for the round's starts: it reads the running daemon's
        # profile, and fails the starts here only for a daemon that it cannot read. A daemon on other profiles is the switch of
        # `daemon_step`, which holds the starts before this. A round whose slots are full starts nothing and calls
        # no preflight.
        if not dry and any(waits is None for _, waits in picks):
            setup, changed = self.setup_halt()
            if setup:
                whole = self.everything_blocked()
                if changed:
                    what = "no start" if whole else f"no start of a cell on {and_list(sorted(self.halt.scope or ()))}"
                    lines.append(Event("halted", "", f"{what} this round: the setup preflight failed", setup))
                if whole:
                    picks = []
                else:  # a failed check of some profiles: their cells wait, the others start
                    held = [by_key[k] for k, w in picks if w is None and self.halt.blocks(self.profile_of(by_key[k]))]
                    lines += self.waits_of(held)
                    picks = [(k, w) for k, w in picks if w or by_key[k] not in held]
        for key, waits in picks:
            if waits:
                why = waits.startswith("no Area") and key in unread
                lines.append(Wait(key, "no Area label, and its origin could not be read", f" ({unread[key]})") if why
                             else Wait(key, waits))
                continue
            woke = self.wake_todo(key, idle, dry)
            if woke is not None:
                lines += woke
                continue
            # The labels as they are now: a label that a run added moves the next start to its cell.
            model, effort, profile = run_settings(by_key[key].get("labels") or [], self.cfg)
            cell = f"({model}, {effort})"
            if dry:
                lines.append(Event("would_start", key, f"would start {key} {cell}", model=model, effort=effort))
            else:
                try:
                    token = self.start_run(key, "start", model, effort, profile=profile)
                except Exception as e:
                    lines.append(self.halt.stop(self.cfg, self.clock(), key, model, e))
                    break
                lines += self.record_start(key, model, effort, token, profile=profile)
        return StartRound(lines, [str(i["identifier"]) for i in running], {str(i["identifier"]): area(i) for i in issues},
                          frozenset(busy), not no_list)

    def wake_todo(self, key: str, idle: set[str], dry: bool) -> list[Event | Wait] | None:
        """The pick `key` is an issue in Todo that a start would take (every wait and limit of `start_decide` held first).
        When its key has an idle session of the orchestrator's label (it asked the owner and waits), the pick gets one wake
        in place of a second session: the open session keeps its context and its worktree. One wake for each move to Todo
        (`todo_wake`, cleared when the issue leaves Todo); the pick still counts against `max_runs` and holds its Area.
        Returns the events (a woken pick gets the line `TODO_WOKEN_WAIT` in each later round, so the plan says why it holds a slot), or None when the pick starts a session: no idle session, or a wake that
        found none or failed (the start goes on in the next round, as before). A dry run sends nothing."""
        if key not in idle or self.todo_wake.get(key) is False:
            return None
        if key in self.todo_wake:
            return [Wait(key, TODO_WOKEN_WAIT)]  # woken already: the session is on it
        if dry:
            return [Event("would_wake", key, f"{key}: Todo again, would wake its idle session", "todo")]
        try:
            sent = self.host.wake(key, TODO_WAKE_TEXT)
        except Exception as e:
            self.todo_wake[key] = False
            return [Event("wake_failed", key, f"wake of the session of {key} failed: {type(e).__name__}: {e}", type(e).__name__)]
        self.todo_wake[key] = sent
        return [Event("woke", key, f"{key}: Todo again, woke its idle session", "todo")] if sent else []

    def waits_of(self, held: Sequence[Mapping[str, Any]]) -> list[Wait]:
        """One line for each issue whose cell runs on a profile that waits: `its cell runs on NAME, which is paused`."""
        return [Wait(str(i["identifier"]), f"its cell runs on {(p := self.profile_of(i))}, which is {self.blocked(p)}") for i in held]

    def hold_back(self, candidates: list[Mapping[str, Any]], lines: list[Event | Wait]) -> list[Mapping[str, Any]]:
        """The candidates whose cell runs on a profile that is not paused or halted: the others get their line (`waits_of`)."""
        held = [i for i in candidates if self.blocked(self.profile_of(i))]
        lines += self.waits_of(held)
        return [i for i in candidates if i not in held]

    def write_again(self, issues: Sequence[Mapping[str, Any]]) -> list[Event | Wait]:
        """Write each run line whose write failed at its start (`unwritten`) once more, while its issue is in Todo. Until
        the run's claim, the run line is the only sign of the start that `.github/linear.py follow_up` reads: without it, a
        review's follow-up findings go into an issue that a run works on already. A key whose issue left Todo
        or is not open any more needs no line: the claim shows the start. A write that fails again gives its event only
        when the error's type is new for the key (`failure`)."""
        todo = {str(i["identifier"]) for i in issues if i.get("state") == self.cfg.todo}
        lines: list[Event | Wait] = []
        for key, line in list(self.unwritten.items()):
            if key not in todo or self.writes is None:
                del self.unwritten[key]
                continue
            try:
                self.writes.comment(key, line)
            except Exception as e:
                lines += self.failure("run_line", key, e, Event("run_line_failed", key, f"run line of {key} failed again: {type(e).__name__}",
                                                                type(e).__name__))
                continue
            del self.unwritten[key]
            self.failed.pop(("run_line", key), None)
            lines.append(Event("run_line_written", key, f"run line of {key} written on a second try"))
        return lines

    def claimed_since_start(self, issue: Mapping[str, Any]) -> bool:
        """A run claimed the issue and gave it back between two rounds, so no round read it In Progress. The sign:
        more comments than the orchestrator's own run line, made since the last start (the run's own run line is one)."""
        began = self.last_start.get(str(issue["identifier"]))
        if began is None:
            return False
        own = 1 if self.writes is not None else 0
        since = [c for c in issue.get("comments") or [] if (made := _seconds(c.get("created"))) is not None and made >= began]
        return len(since) > own

    def backing_off(self, step: str, key: str) -> bool:
        """True while a read or close of `key` that failed is less than an hour old."""
        found = self.failed.get((step, key))
        return found is not None and self.clock() - found[0] < HOUR

    def failure(self, step: str, key: str, error: Exception, event: Event) -> list[Event]:
        """Keep the time of a failed try; give `event` only when the error's type is new for this step and key."""
        before = self.failed.get((step, key))
        self.failed[(step, key)] = (self.clock(), type(error).__name__)
        return [] if before is not None and before[1] == type(error).__name__ else [event]

    def issue_states(self, keys: Sequence[str], open_issues: Mapping[str, str],
                     final: Final) -> tuple[dict[str, dict[str, Any]], list[Event]]:
        """The state of each key, and the time it entered a final state. A key the round read is open: no read. A final
        state is kept until the key shows among the open issues again (reopened); any other state is read again after an
        hour. So a night of finished runs costs one or two reads each, not one per round. A failed read keeps the workspace and is tried again after an hour (`failed`); its event
        prints only when the error's type is new for the key, and a success clears it."""
        now = self.clock()
        for key in [k for k in self.finals if k in open_issues]:
            del self.finals[key]
        states: dict[str, dict[str, Any]] = {k: {"state": s, "since": None, "labels": ()} for k, s in open_issues.items()}
        events: list[Event] = []
        for key in dict.fromkeys(keys):
            if key in states:
                continue
            cached = self.finals.get(key)
            if cached is None or (cached[0] not in self.cfg.closed_states and now - cached[2] >= HOUR):
                if self.backing_off("read", key):
                    continue
                try:
                    state, since, labels = final(key)
                except Exception as e:  # one issue that cannot be read keeps its workspace; the others go on
                    events += self.failure("read", key, e, Event(
                        "close_failed", key, f"close of the workspace of {key}: reading the issue failed: {type(e).__name__}",
                        type(e).__name__))
                    continue
                self.failed.pop(("read", key), None)
                cached = self.finals[key] = (state, since, now, labels)
            states[key] = {"state": cached[0], "since": cached[1], "labels": cached[3]}
        return states, events

    def close_events(self, issues: Sequence[Mapping[str, Any]], dry: bool,
                     final: Final | None) -> list[Event]:
        """Close the workspaces that `to_close` returns, one event each. A failed list gives an event every round; a failed close
        is tried again after an hour and gives an event, with the error's type, only when that type is new for it (a
        success clears it). The round goes on. A dry run closes nothing and says `would close` once per workspace."""
        if self.cfg.close_after_hours <= 0 or final is None:
            return []
        try:
            spaces = self.host.workspaces()
        except Exception as e:
            return [Event("close_failed", "", f"closing workspaces: the list failed: {type(e).__name__}", type(e).__name__)]
        prefix = f"{self.cfg.team.upper()}-"  # a workspace of another team's key belongs to that team's runner
        spaces = [s for s in spaces if (space_key(s) or "").startswith(prefix)]
        keys = [k for s in spaces if (k := space_key(s)) and str(s.get("agent_status") or "") in FINISHED]
        states, events = self.issue_states(keys, {str(i["identifier"]): str(i.get("state")) for i in issues}, final)
        for c in to_close(spaces, states, self.clock(), self.cfg.close_after_hours, self.cfg.closed_states):
            why = f"{c.state} {c.hours} h ago"
            if dry or self.writes is None:
                if c.workspace not in self.told:
                    self.told.add(c.workspace)
                    events.append(Event("would_close", c.key, f"would close the workspace of {c.key} ({why})"))
                continue
            if self.backing_off("close", c.workspace):
                continue
            try:
                self.host.close(c.workspace)
            except Exception as e:  # the next try comes after an hour
                events += self.failure("close", c.workspace, e, Event(
                    "close_failed", c.key, f"close of the workspace of {c.key} failed: {type(e).__name__}", type(e).__name__))
                continue
            self.failed.pop(("close", c.workspace), None)
            events.append(Event("closed", c.key, f"closed the workspace of {c.key} ({why})"))
        return events

    def drop_events(self, issues: Sequence[Mapping[str, Any]], prs: Sequence[Mapping[str, Any]], dry: bool,
                    final: Final | None) -> list[Event]:
        """Close the open pull requests that `to_drop` returns, each with one comment. The states come from
        `issue_states` (its cache); before a close, the issue is read again, so a cancel that the owner undid after the cache
        read keeps its pull request. A failed read follows `issue_states` (an hour's wait, an event only for a new error type); a
        failed close gives an event with the error's type, and the round goes on. A dry run closes nothing and says `would close PR N (KEY canceled)` once per pull request. The pull request of an issue with
        a skip label is never closed: it goes to `by_hand`, the plan's row `yours`, from the cache, read again once an hour
        (the owner's call, 2026-11-11)."""
        self.by_hand = []
        if final is None:
            return []
        keys = [k for p in prs if (k := team_key(p.get("headRefName"), self.cfg.team))]
        states, events = self.issue_states(keys, {str(i["identifier"]): str(i.get("state")) for i in issues}, final)
        events = [Event("close_pr_failed", e.key, f"close of the pull request of {e.key}: reading the issue failed: "
                        f"{e.reason}", e.reason) for e in events]
        for d in to_drop(prs, states, self.clock(), self.cfg.canceled, self.cfg.cancel_grace_minutes, self.cfg.team,
                         self.cfg.skip_labels):
            if d.label is not None:
                if self.clock() - self.finals[d.key][2] >= HOUR:
                    # The row comes from the cache: read the issue again once an hour, so a cancel that the owner undid
                    # (to a state the round does not read) takes the row away.
                    try:
                        state, since, labels = final(d.key)
                    except Exception as e:
                        events.append(Event("close_pr_failed", d.key, f"close of PR {d.number}: reading {d.key} failed: "
                                            f"{type(e).__name__}", type(e).__name__))
                        self.by_hand.append(d)  # the last known state stays until a read works
                        continue
                    self.finals[d.key] = (state, since, self.clock(), labels)
                    again = next(iter(to_drop([{"number": d.number, "headRefName": d.key.lower()}],
                                          {d.key: {"state": state, "since": since, "labels": labels}}, self.clock(),
                                          self.cfg.canceled, self.cfg.cancel_grace_minutes, self.cfg.team,
                                          self.cfg.skip_labels)), None)
                    if again is None:
                        continue
                    d = again
                if d.label is not None:
                    self.by_hand.append(d)
                    continue
            if (dry or self.writes is None or self.writes.close is None) and d.number in self.told_prs:
                continue
            try:
                state, since, labels = final(d.key)  # read again: the owner may have undone the cancel since the cached read
            except Exception as e:
                events.append(Event("close_pr_failed", d.key, f"close of PR {d.number}: reading {d.key} failed: "
                                    f"{type(e).__name__}", type(e).__name__))
                continue
            self.finals[d.key] = (state, since, self.clock(), labels)
            fresh = to_drop([{"number": d.number, "headRefName": d.key.lower()}],
                            {d.key: {"state": state, "since": since, "labels": labels}}, self.clock(), self.cfg.canceled,
                            self.cfg.cancel_grace_minutes, self.cfg.team, self.cfg.skip_labels)
            if not fresh:
                continue
            if fresh[0].label is not None:
                self.by_hand.append(fresh[0])
                continue
            if dry or self.writes is None or self.writes.close is None:
                self.told_prs.add(d.number)
                events.append(Event("would_close_pr", d.key, f"would close PR {d.number} ({d.key} canceled)"))
                continue
            try:
                self.writes.close(d.number, drop_comment(d.key))
            except Exception as e:  # the next round tries again
                events.append(Event("close_pr_failed", d.key, f"close of PR {d.number} failed: {type(e).__name__}",
                                    type(e).__name__))
                continue
            events.append(Event("closed_pr", d.key, f"closed PR {d.number} ({d.key} canceled {d.minutes} min ago)"))
        return events

    def plan(self, issues: Sequence[Mapping[str, Any]], prs: Sequence[Mapping[str, Any]], dry: bool,
             labels: Callable[[str], list[str]] | None = None, branch: Callable[[int], str] | None = None,
             final: Final | None = None, token: object | None = None) -> tuple[dict[str, Any], list[Event]]:
        """One round's decisions and actions (the setup failures, the host's pause, the escalations, the starts, the closes of
        workspaces, then the closes of the pull requests of canceled issues), then its plan (`build_plan`) and its events in order.
        `token` names the round for the shared stop: a team after the first copies the stop that its round's first team read."""
        events = [] if dry else self.setup_failure_events(issues)
        self.halt.claim_waited(issues, self.clock(), self.cfg.todo)
        events += self.pause_events(dry, token)
        events += self.fix_events(issues, prs, dry)
        decided = self.start_decide(issues, prs, dry, labels, branch)
        events += [e for e in decided.items if isinstance(e, Event)]
        events += self.close_events(issues, dry, final)
        events += self.drop_events(issues, prs, dry, final)
        stamp = datetime.fromtimestamp(self.clock(), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        # A session that this process started and that still runs is busy too: a plain process shows in no list (`sessions_read`)
        busy = (decided.busy | {k for k, started in self.live.items() if self.host.alive(started.session)}) if decided.listed else None
        plan = build_plan(issues, prs, self.cfg, stamp=stamp, slots=self.held(issues, prs, decided.busy), running=decided.running,
                          areas=decided.areas, events=events, waits=[w for w in decided.items if isinstance(w, Wait)],
                          stacks=stack_pairs(issues, prs, self.cfg), by_hand=self.by_hand, busy=busy, pause=self.pause_plan())
        return plan, events


# -- The health check: one line per check, never a key and never an issue's text. --------------------------------

OK, WARN, FAIL = "ok", "WARN", "FAIL"
Line = tuple[str, str]
# The files that the install copies (orchestrator/deploy/install.py FILES; a test keeps the two lists equal).
INSTALLED_FILES = ("orchestrator/orchestrator.py", "orchestrator/deploy/service.py", ".github/linear.py", ".workflow/agent_user.py",
                   "orchestrator/model-catalog.json")
WAITING_LINE = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ) waiting:")
LOG_TAIL_BYTES = 256 * 1024
DAY = 86400


def _utc(stamp: str, shape: str) -> float:
    return datetime.strptime(stamp, shape).replace(tzinfo=UTC).timestamp()


def _when(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def check_keys(environ: Mapping[str, str], cfgs: Sequence[Config]) -> list[Line]:
    """One line for each team: its Linear key's variable is set in the environment or not. Never a value."""
    return [(OK, f"keys: {c.team}: {c.linear_key_env} is set") if environ.get(c.linear_key_env, "").strip()
            else (FAIL, f"keys: {c.team}: {c.linear_key_env} is not set") for c in cfgs]


def check_apps(cfgs: Sequence[Config], visibility: Callable[[str], str],
               app_repos: Callable[[], Sequence[str]] = lambda: ()) -> list[Line]:
    """A public repository's implementer app must reach that repository only (agents/README.md "Setting up a product"):
    its private key can mint a token for each repository that the app is installed on. Two reads give a WARN:

    - `app_repos`: the repositories of the installation of the service's app (its `gh` uses the implementer app's
      installation token: `GET /installation/repositories`). A public product whose repository is in that list, when
      the list holds more than that one repository, gets `the app NAME is installed on N repositories`.
    - The config: two products whose `conflict_author` names one app, one of them public: the public one gets `the app
      NAME is also the app of OTHER`.

    The service holds no key of the reviewer app, so the check cannot see the reviewer app's installations. It reads the
    visibility of a repository only when one of the two cases needs it."""
    lines: list[Line] = []
    seen: dict[str, bool | None] = {}

    def public(c: Config) -> bool | None:
        if c.repo not in seen:
            try:
                seen[c.repo] = visibility(c.repo) == "public"
            except Exception as e:
                lines.append((WARN, f"{c.team}: cannot read the visibility of {c.repo} ({type(e).__name__})"))
                seen[c.repo] = None
        return seen[c.repo]

    try:
        repos = {r.lower() for r in app_repos()}
    except Exception as e:
        lines.append((WARN, f"apps: cannot list the repositories of the service's app ({type(e).__name__})"))
        repos = set()
    warned: set[str] = set()
    if len(repos) > 1:
        for c in cfgs:
            if c.repo.lower() in repos and public(c):
                warned.add(c.team)
                lines.append((WARN, f"{c.team}: the app {c.conflict_author or 'of the service'} is installed on {len(repos)} "
                                    "repositories: a public repository's app must reach it only"))
    by_app: dict[str, list[Config]] = {}
    for c in cfgs:
        if c.conflict_author:
            by_app.setdefault(c.conflict_author, []).append(c)
    for app, group in by_app.items():
        for c in group if len(group) > 1 else ():
            if c.team not in warned and public(c):
                others = ", ".join(o.team for o in group if o is not c)
                lines.append((WARN, f"{c.team}: the app {app} is also the app of {others}: a public repository's app must reach it only"))
    count = f"{len(repos)} repositor{'y' if len(repos) == 1 else 'ies'}"
    return lines or [(OK, f"apps: no public repository shares its implementer app (the service's app is installed on {count})")]


def check_answer(name: str, read: Callable[[], object]) -> Line:
    """One small read. A failure prints the error's type only: its text can hold a reply's content."""
    try:
        read()
    except Exception as e:
        return FAIL, f"{name}: no answer ({type(e).__name__})"
    return OK, f"{name}: answers"


def round_time(state: str | None) -> float | None:
    """The time of the last round in the state file's text, good or failed, or None."""
    try:
        plan = json.loads(state or "")
    except ValueError:
        return None
    if not isinstance(plan, dict):
        return None
    failed = plan.get("failed")
    times: list[float] = []
    for stamp in (plan.get("time"), failed.get("time") if isinstance(failed, dict) else None):
        try:
            times.append(_utc(str(stamp), "%Y-%m-%dT%H:%M:%SZ"))
        except ValueError:
            continue
    return max(times, default=None)


# The keychain lookup that the service waits on fails in the service's own session, the audit session of the
# agents' user domain. A pane has a session of its own after a restart, so the unlock goes through `launchctl asuser`
# (`owlcode unlock`); the install's own lines below name an older LaunchDaemon, whose session no unlock reaches.
KEYCHAIN_LOCKED = ("the service: the agent's keychain is locked in the service's session: from your account, run "
                   "`owlcode unlock` (an unlock in a pane does not reach the service); when the "
                   "waiting lines go on, read the line on an older LaunchDaemon below")


def check_service(log: str | None, state: str | None, now: float, poll_seconds: int) -> Line:
    """The service: the state file's round is younger than three poll periods, and no `waiting:` line of the log is newer.
    The log prints only changes, so the round's time comes from the state file, which each round writes."""
    if log is None:
        return FAIL, "the service: no log: is it installed?"
    waits = [_utc(m.group(1), "%Y-%m-%dT%H:%M:%SZ") for line in log.splitlines() if (m := WAITING_LINE.match(line))]
    last_round, last_wait = round_time(state), max(waits, default=None)
    if last_wait is not None and (last_round is None or last_wait > last_round):
        return FAIL, KEYCHAIN_LOCKED
    if state is None:
        return FAIL, "the service: no state file: is it installed?"
    if last_round is None:
        return FAIL, "the service: the state file holds no round"
    if now - last_round > 3 * poll_seconds:
        return FAIL, f"the service: no round since {_when(last_round)}"
    return OK, f"the service: last round at {_when(last_round)}"


def check_round(state: str | None, now: float) -> Line | None:
    """A line that tells of a round that runs: the state file holds its start, newer than its plan, and the process that wrote it still runs. A slow round is no
    stopped service."""
    try:
        plan = json.loads(state or "")
    except ValueError:
        return None
    text = running_text(plan, now) if isinstance(plan, dict) else None
    return (OK, f"the service: {text}") if text else None


def check_sessions(sessions: Sequence[Mapping[str, Any]], now: float,
                   run_started: Callable[[str], float | None], current: str | None = None) -> list[Line]:
    """A workspace that the orchestrator labelled (`KEY ·`) whose agent still works a day after its start. The start is
    the time of the newest run line on its issue (`run_started`): the label holds no time. A session whose issue
    has no run line, or cannot be read, gives a WARN that says so. A session of an old host (its `host` is not `current`) names that host in its lines."""
    lines: list[Line] = []
    for session in sessions:
        key = own_key(session.get("label"))
        if key is None or str(session.get("agent_status") or "") not in BUSY:
            continue
        name = f"{key} (on {host})" if (host := session.get("host")) and host != current else key
        try:
            started = run_started(key)
        except Exception as e:
            lines.append((WARN, f"sessions: {name} works, and its run line could not be read ({type(e).__name__})"))
            continue
        if started is None:
            lines.append((WARN, f"sessions: {name} works, and its issue has no run line: its age is unknown"))
        elif now - started > DAY:
            lines.append((WARN, f"sessions: {name} still works, {int((now - started) // 3600)} hours after its start"))
    return lines or [(OK, "sessions: none works for more than a day")]


def run_started(comments: Sequence[Mapping[str, Any]]) -> float | None:
    """The start of a run: when the newest comment that begins with `Run: ` (`run_line`) was made, else None."""
    times = [t for c in comments if str(c.get("body") or "").startswith("Run: ") and (t := _seconds(c.get("created"))) is not None]
    return max(times, default=None)


def check_installed(commit: str | None, missing: Sequence[str] | None) -> Line:
    """The installed copy: no commit of origin/main under the installed files is missing from it. `missing` is None when
    the installed commit is not a commit of the clone (an install from another repository's checkout)."""
    if not commit:
        return WARN, "installed: no COMMIT in the installed folder: run owlcode install"
    if missing is None:
        return WARN, "installed: the installed version is not a commit of this checkout: run owlcode install"
    if missing:
        return WARN, f"installed: {commit[:12]} lacks {len(missing)} commits of main under its files: run owlcode install"
    return OK, f"installed: {commit[:12]} is current"


ADMIN_COMMAND = "owlcode admin"  # takes the agents' user from `[service] user` of the installed config (OWL-54)


def check_home_copy(home_copy: bytes | None, installed_copy: bytes) -> Line | None:
    """The agents' home copy of `agent_user.py` (`admin` writes it; the panes' `claude`, `agent` and `pi` aliases run it) equals the
    installed one. An old copy gives `--workers N` to the harness as its own argument, and nothing else warns. None: no home copy.
    `owlcode admin` takes the agents' user from `[service] user`, so the step names no user."""
    if home_copy is None:
        return None
    if home_copy == installed_copy:
        return OK, "agent_user.py: the home copy is the installed one"
    return WARN, ("agent_user.py: the agents' home copy differs from the installed one, so a pane's claude or pi can start with "
                  f"old options: run {ADMIN_COMMAND} from a "
                  "checkout at the installed commit, or owlcode install when the checkout is newer")


def read_home_copy(home: Path, installed: Path) -> Line | None:
    try:
        home_copy = (home / "agent_user.py").read_bytes()
    except FileNotFoundError:
        home_copy = None
    return check_home_copy(home_copy, installed.read_bytes())


def check_command(cfg: Config) -> Line:
    key = "EXAMPLE-1"
    model, effort, profile = run_settings([], cfg)
    try:
        cwd = Path(cfg.run_cwd).expanduser()
    except RuntimeError:
        return FAIL, "example start command: cannot expand the home directory in run_cwd"
    try:
        command = format_run(cfg, key, "start", model, effort, session_label(key), str(cwd), "20000101T000000Z-example", profile)
    except ConfigError as e:
        return FAIL, f"example start command: {e}"
    return OK, f"example start command: {command}"


def check_token(cfg: Config) -> Line:
    """A run's command without `{token}` (in `run_command`, or in the harness's `skill_prompt` that `{prompt}` puts there)
    gives no start token to its sessions, so its run lines carry none and another starter cannot see an unclaimed start.
    A WARN: the runs still start, as before."""
    if not uses(cfg, "token"):
        return WARN, ('start prompt: no {token}: put "start {token}" in the skill_prompt of '
                      f"[harnesses.{cfg.harness}] (orchestrator.example.toml)")
    return OK, "start prompt: gives each session its start token"


def check_review(cfg: Config) -> Line:
    """The local review of the inference profile in force: its model and effort level, which the config's
    `[profiles.NAME]` table names and `owlcode install` (or `restart`) writes beside the installed copy of
    `agent_user.py`, which reads it. One model for each profile, so the reviews of a profile are comparable."""
    model, effort = cfg.review
    return OK, (f"local review: {model}, effort {effort or 'none'} (the {cfg.inference_profile} profile): "
                "`owlcode restart` writes a change of it beside the installed agent_user.py")


def _credential_line(what: str, signed_in: bool, has_key: Callable[[], bool]) -> Line:
    """Whether a profile's credential is there (`key_check`: a keychain item, whose value is never read into this process
    or printed, or a sign-in that the harness renews itself). `what` names the profile at the line's start."""
    try:
        found = has_key()
    except Exception as e:
        found, why = False, f" ({type(e).__name__})"
    else:
        why = ""
    if found:
        return OK, f"{what}, " + ("its sign-in is there" if signed_in else "its key is in the keychain")
    if signed_in:
        return FAIL, (f"{what}, sign-in is not set up{why}: sign pi in as the agents' user (`pi`, then "
                      "`/login`); `agent_user.py pi-signin --provider NAME` shows what pi holds")
    return FAIL, (f"{what}, its key is not in the keychain{why}: store it as the agents' user "
                  "with `agent_user.py user` (README.md \"The agents' macOS user\")")


def check_profile(cfg: Config, has_key: Callable[[], bool]) -> list[Line]:
    """The inference profile in force: its name, and whether its credential is there (`_credential_line`)."""
    return [_credential_line(f"inference profile: {cfg.inference_profile}", cfg.credential == "sign-in", has_key)]


def cell_groups(cfg: Config) -> list[tuple[str, list[str]]]:
    """Each profile other than the one in force that a cell runs on, with its cells in the order of CELLS."""
    groups: dict[str, list[str]] = {}
    for cell, profile in zip(CELLS, cfg.cell_profiles, strict=False):  # empty: no cell on another profile
        if profile != cfg.inference_profile:
            groups.setdefault(profile, []).append(cell)
    return list(groups.items())


def check_cell_profiles(cfg: Config, has_key: Callable[[str], bool]) -> list[Line]:
    """The credential of each other profile that a cell runs on: `cell profile: NAME (CELLS), ...`."""
    runs = dict(cfg.cell_runs)
    return [_credential_line(f"cell profile: {name} ({', '.join(cells)})", runs[name].credential == "sign-in",
                             partial(has_key, name)) for name, cells in cell_groups(cfg)]


def refused_mix(cfg: Config) -> list[tuple[str, list[str]]]:
    """Each profile (with its cells) that a cell runs on and the session host cannot run yet."""
    runs = dict(cfg.cell_runs)
    return [(name, cells) for name, cells in cell_groups(cfg) if (cfg.session_host, runs[name].harness[0]) not in RUN_TEMPLATES]


def check_mix(cfg: Config) -> list[Line]:
    """A mix that the session host cannot run yet fails. Paseo runs it: its daemon holds the keys of every profile
    (`check_paseo_profile`). herdr starts Claude Code and pi, and the plain process only Claude Code."""
    runs = dict(cfg.cell_runs)
    started = [harness for host, harness in RUN_TEMPLATES if host == cfg.session_host]  # the harnesses that the host starts
    which = "that harness" if len(started) == 1 else "one of those harnesses"
    return [(FAIL, f"cell profile: {name} ({', '.join(cells)}) runs on {runs[name].harness[0]}, and {cfg.session_host} "
                   f"starts only {and_list(started)}: each cell must use a profile of {which}")
            for name, cells in refused_mix(cfg)]


def paseo_cli(home: Path) -> str:
    """The agent's link to the Paseo app's CLI (`~/.local/bin/paseo`): `--preflight` runs with a system PATH, and the link stands
    there."""
    cli = home / ".local/bin/paseo"
    return str(cli) if cli.exists() else "paseo"


def paseo_record(home: Path, run: Run = run_quiet) -> dict[str, Any] | tuple[()] | None:
    """The record from agent_user.py of the running daemon, only when it matches that daemon (its pid and start time). An empty
    tuple: no daemon runs. None: the status cannot be read, or the running daemon has no record of its own."""
    done = run([paseo_cli(home), "daemon", "status", "--json"])
    if done.returncode:
        return None
    status = json.loads(done.stdout)
    if isinstance(status, dict) and status.get("localDaemon") == "stopped":
        return ()
    if not isinstance(status, dict) or status.get("localDaemon") != "running":
        return None
    pid, started = status.get("pid"), status.get("startedAt")
    if type(pid) is not int or not isinstance(started, str) or not started:
        return None
    try:
        marker = json.loads((home / ".local/state/orchestrator/paseo-profile.json").read_text())
    except (OSError, ValueError):  # no record: a daemon that `paseo-daemon` did not start
        return None
    return marker if isinstance(marker, dict) and marker.get("pid") == pid and marker.get("startedAt") == started else None


def read_paseo_profile(home: Path, run: Run = run_quiet) -> tuple[str, ...] | None:
    """Read the daemon's profiles only when the record from agent_user.py matches the running daemon: the profile whose review
    the daemon holds first, then the others whose keys it holds. A record from before the cells on other profiles has the one `profile`.
    An empty tuple: no daemon runs (the service starts it). None: the status cannot be read, or the running daemon
    has no record of its own."""
    marker = paseo_record(home, run)
    if not isinstance(marker, dict):
        return marker
    names = marker.get("profiles", [marker.get("profile")])
    known = _model_catalog()["profiles"]
    return tuple(names) if isinstance(names, list) and names and all(isinstance(n, str) and n in known for n in names) else None


def review_digest(path: Path, names: Sequence[str]) -> str | None:
    """A digest of the entries of `names` in `local-review.json` (the model, the effort level and the cells of each profile that
    the daemon holds). `agent_user.py` computes the same digest at the daemon's start and records it, so a change of
    the review settings alone is a difference that the service sees. None: no file, or a file that holds no such entry."""
    try:
        found = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(found, dict) or not all(name in found for name in names):
        return None
    return hashlib.sha256(json.dumps({name: found[name] for name in names}, sort_keys=True).encode()).hexdigest()


def and_list(names: Sequence[str]) -> str:
    """`a`, `a and b`, `a, b and c`."""
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


# The owner's call of 2026-11-23: the config is the one place of the inference profile, and the service applies it to
# Paseo's daemon, so the owner runs nothing as the agents' user. A stop of the daemon kills the agents in a turn.
WORKING = frozenset({"initializing", "running"})  # Paseo's statuses of an agent in a turn
DAEMON_START_SECONDS = 180  # `paseo-daemon` waits up to 60 s for Tailscale's address, then starts the daemon
SWITCHING = "switching to {profile}: waiting for {count} agent{s}"
# The known causes of a failed start, as `Halt.stop` keeps them: the cause reaches a Linear notice, and raw output never does.
DAEMON_HINTS = ((r"Tailscale is not connected", "Tailscale is not connected"), (r"not in the Keychain", "a key is not in the Keychain"),
                (r"is not the orchestrator's inference_profile|names no profile|cannot read .*inference-profile",
                 "the installed inference-profile.json differs: run `owlcode restart`"),
                (r"two values|leaves it unset|sets .* and .* does not", "two profiles of the mix clash"))


Spawn = Callable[[Sequence[str], dict[str, str]], "subprocess.CompletedProcess[str]"]


def spawn_daemon(argv: Sequence[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run the daemon's start in a session of its own: launchd ends the process group of a job that ends, and the daemon must
    outlive a restart of the service. Paseo's supervisor leaves the pipes (its stdio is /dev/null, measured 2026-11-24)."""
    return subprocess.run(list(argv), stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=DAEMON_START_SECONDS,
                          check=False, start_new_session=True, env=env)


class PaseoDaemon:
    """The local Paseo daemon as the service sees it: its profiles (`read_paseo_profile`), its agents in a turn, a
    stop, and a start through the installed `agent_user.py paseo-daemon --profile NAME`, which reads the keys itself.
    The start's environment lacks `hide`, the variable of each team's Linear key: the service holds a second
    workspace's key, and each agent of the daemon would inherit it (review R1-1). `paseo-daemon` sets LINEAR_API_KEY itself."""

    def __init__(self, profile: str, expected: Sequence[str], host: PaseoHost, home: Path | None = None,
                 spawn: Spawn = spawn_daemon, hide: Iterable[str] = (), review_file: Path | None = None) -> None:
        self.profile, self.expected, self.host = profile, tuple(expected), host
        self.home, self.run, self.spawn, self.hide = home or Path.home(), host.run, spawn, frozenset(hide)
        self.review_file = review_file  # the installed `local-review.json`; None: the review settings are no check (a test)

    def review_matches(self) -> bool:
        """The running daemon started on the review settings that the file holds now (its record holds their digest).
        True when no file is set or the file cannot be read: the service never stops a daemon for a setting that it cannot see."""
        if self.review_file is None or (want := review_digest(self.review_file, self.expected)) is None:
            return True
        record = paseo_record(self.home, self.run)
        return isinstance(record, dict) and record.get("review") == want

    def profiles(self) -> tuple[str, ...] | None:
        """`read_paseo_profile`: an empty tuple when no daemon runs, None for a running daemon with no record of its own. A
        status that cannot be read raises: the service never stops a daemon that it cannot read."""
        found = read_paseo_profile(self.home, self.run)
        if found is None and self.run([paseo_cli(self.home), "daemon", "status", "--json"]).returncode:
            raise RuntimeError("cannot read the daemon's status")
        return found

    def working(self) -> int:
        try:
            agents = self.host._agents(background=True)
        except (RuntimeError, ValueError):  # its text holds raw output, and the halt's cause reaches a Linear notice
            raise RuntimeError("cannot list the agents") from None
        return sum(1 for a in agents if a.get("status") in WORKING)

    def stop(self) -> None:
        done = self.run([paseo_cli(self.home), "daemon", "stop"])
        if done.returncode:
            raise RuntimeError(f"paseo daemon stop exited {done.returncode}")

    def start(self) -> None:
        env = {name: value for name, value in os.environ.items() if name not in self.hide}
        done = self.spawn(["/usr/bin/python3", "-I", str(agent_wrapper()), "paseo-daemon", "--profile", self.profile], env)
        if done.returncode:
            said = f"{done.stdout}\n{done.stderr}"
            raise RuntimeError(next((hint for pattern, hint in DAEMON_HINTS if re.search(pattern, said)), f"exit {done.returncode}"))


def daemon_step(cfg: Config, halt: Halt, daemon: PaseoDaemon, now: float, emit: Callable[[str], None]) -> str | None:
    """Keep Paseo's daemon on the profile in force, once in each round before the starts. A daemon that holds the
    expected profiles lifts the daemon's halt. No daemon: start one (also the first round after a restart of the Mac). A daemon
    on other profiles, or with no record of them: halt the starts with the cause `switching to NAME: waiting for N agents`
    while agents work, and with none, stop it and start it again. Wakes, closes and the queue go on. A failed step keeps the
    halt with its cause, which notifies the owner, and the next round tries again. Returns the resume line."""
    def lift() -> str | None:
        held, halt.daemon_hold = halt.daemon_hold, False
        if not held or halt.owner_hold or halt.cause is None:
            return None
        return halt.resume(now, f"the Paseo daemon runs on {daemon.profile}")

    try:
        actual = daemon.profiles()
        if daemon_matches(daemon.expected, actual) and daemon.review_matches():
            return lift()
        if actual != () and (count := daemon.working()):
            if not halt.owner_hold:  # the owner's pause stays the banner; the switch waits all the same
                halt.enter(cfg, now, SWITCHING.format(profile=daemon.profile, count=count, s="s"[:count != 1]))
                halt.daemon_hold, halt.unreported = True, None  # the owner asked for the switch: no notice
            return None
        if actual != ():
            daemon.stop()
            held = ", ".join(actual or ("no record",))
            emit(f"Paseo daemon stopped: it held {held}"
                 f"{' with other review settings' if daemon_matches(daemon.expected, actual) else ''}, and no agent works")
        daemon.start()  # `paseo-daemon` checks that the daemon runs, and records its profiles
        # Its profiles come from `local-review.json`, the expected ones from the config the service read: a difference (a
        # config edit with no `restart`) would restart the daemon each idle round with no notice (review R1-3)
        if not daemon_matches(daemon.expected, started := daemon.profiles()):
            raise RuntimeError(f"the started daemon holds {', '.join(started or ('no record',))}, expected "
                               f"{and_list(daemon.expected)}: run `owlcode restart`")
        if not daemon.review_matches():
            raise RuntimeError("the started daemon holds other review settings than the installed local-review.json: run "
                               "`owlcode restart`")
        emit(f"Paseo daemon started on {and_list(daemon.expected)}")
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as e:
        cause = f"Paseo daemon not started on {daemon.profile}: {e if isinstance(e, RuntimeError) else type(e).__name__}"
        if not halt.owner_hold and halt.cause != cause:
            halt.enter(cfg, now, cause)
        halt.daemon_hold = True
        return None
    return lift()


def daemon_matches(expected: Sequence[str], actual: Sequence[str] | None) -> bool:
    """The daemon holds the key of each expected profile, and the review of the first (its `LOCAL_REVIEW_COMMAND`)."""
    return bool(actual) and actual is not None and actual[0] == expected[0] and set(expected) <= set(actual)


def check_paseo_profile(expected: Sequence[str], read: Callable[[], Sequence[str] | None], user: str = "") -> Line:
    """The daemon's profiles. `expected` is `daemon_profiles`: the profile in force, then each other that a cell
    names. A daemon that is stopped or on other profiles is a WARN: the service starts it, or switches it when no
    agent runs (`daemon_step`), and holds the starts until then. Only a status that cannot be read fails."""
    try:
        actual = read()
    except (OSError, ValueError, ConfigError, subprocess.SubprocessError):
        actual = None
    word = "profiles" if len(expected) > 1 or (actual and len(actual) > 1) else "profile"
    if daemon_matches(expected, actual):
        return OK, f"Paseo daemon {word}: {', '.join(actual or ())}"
    if actual == ():
        return WARN, f"Paseo daemon {word}: no daemon runs; the service starts it on {and_list(expected)}"
    if actual is None:
        who = f"as {user}, run" if user else "as the agents' user, run"
        return FAIL, (f"Paseo daemon {word}: cannot verify the running daemon: {who} `paseo daemon status --json` and read its "
                      "output; the service stops a daemon with no record of its profiles only when no agent runs")
    want = f"{expected[0]} first (its local review)" if set(expected) <= set(actual) else and_list(expected)
    return WARN, f"Paseo daemon {word}: {', '.join(actual)}, expected {want}; the service switches it when no agent runs"


def pi_model_exists(cfg: Config, model: str) -> bool:
    """Whether pi lists the model (with its provider's prefix) offline. A cell may run on another pi profile than the one
    in force, so each pi provider of the catalog gets its dummy key."""
    catalog = _model_catalog()
    variables = sorted({connection["key_variable"] for provider in catalog["providers"].values()
                        for harness, connection in provider["connections"].items()
                        if harness == "pi" and connection.get("key_variable")})
    # Launchd starts with a system PATH. Pi's launcher needs the agents' mise Node.
    home = Path.home()
    path = os.pathsep.join([str(home / ".local/bin"), str(home / ".local/share/mise/shims"), os.environ.get("PATH", os.defpath)])
    env = ["/usr/bin/env", f"PATH={path}"]
    # Pi lists only available models. Give custom providers a dummy key, not the real key.
    env += [f"{variable}=offline-preflight" for variable in variables]
    done = run_quiet([*env, "pi", "--offline", "--no-approve", "--list-models"])
    return done.returncode == 0 and any("/".join(row.split()[:2]) == model for row in done.stdout.splitlines())


def preflight(cfg: Config, has_key: Callable[[], bool], has_model: Callable[[str], bool],
              paseo_profile: Callable[[], Sequence[str] | None] = lambda: None,
              cell_key: Callable[[str], bool] = lambda _: False) -> list[Check]:
    """Check the selected profile without opening a session or calling a model: the key, the key of each other profile
    that a cell runs on and a mix that the host cannot run, the local Paseo daemon's profile when the host is Paseo and a
    product's sessions run there (a product with no `paseo_host`: another daemon's user keeps that daemon's profile),
    then the models that pi lists offline: each cell's on a pi profile, and the review's when the profile in
    force runs on pi. Each line is a `Check` with the profiles it belongs to: a failed one halts only their cells.
    A profile with a failed credential gets no model check, and the others do."""
    lines = [Check(*line, frozenset({cfg.inference_profile})) for line in check_profile(cfg, has_key)]
    lines += [Check(*line, frozenset({name})) for (name, _), line in zip(cell_groups(cfg), check_cell_profiles(cfg, cell_key), strict=True)]
    lines += [Check(*line, frozenset({name})) for (name, _), line in zip(refused_mix(cfg), check_mix(cfg), strict=True)]
    if cfg.session_host == "paseo" and any(not c.endpoint.host for c in team_configs(cfg)):
        expected = daemon_profiles(cfg)
        try:
            actual = paseo_profile()
        except (OSError, ValueError, ConfigError, subprocess.SubprocessError):
            actual = None
        # A daemon that cannot be read fails every start. One on other profiles is the service's switch.
        lines.append(Check(*check_paseo_profile(expected, lambda: actual, cfg.service_user)))
    if any(c.level == FAIL and c.profiles is None for c in lines):
        return lines
    broken = set().union(*(c.profiles for c in lines if c.level == FAIL and c.profiles is not None))
    profiles = cfg.cell_profiles or (cfg.inference_profile,) * len(CELLS)
    harness = {cfg.inference_profile: cfg.harness, **{name: run.harness[0] for name, run in cfg.cell_runs}}
    checks = [(profile, cell, target) for cell, (target, _), profile in zip(CELLS, cfg.run_cells, profiles, strict=True)
              if harness[profile] == "pi" and profile not in broken]
    if cfg.harness == "pi" and cfg.inference_profile not in broken:
        checks.append((cfg.inference_profile, "review", cfg.review[0]))
    lines += [Check(OK, f"{name}: credential check only (no offline model lookup)")
              for name in dict.fromkeys(harness[p] for p in (cfg.inference_profile, *profiles) if p not in broken) if name != "pi"]
    for profile, cell, target in checks:
        try:
            found = has_model(target)
        except (ConfigError, OSError, subprocess.SubprocessError) as e:
            lines.append(Check(FAIL, f"{profile} {cell}: pi could not list {target} ({type(e).__name__})", frozenset({profile})))
        else:
            lines.append(Check(OK if found else FAIL, f"{profile} {cell}: {target} {'is available' if found else 'not found in pi'}",
                               frozenset({profile})))
    return lines


def profile_key(cfg: Config) -> bool:
    return run_quiet(shlex.split(cfg.profile_key_check)).returncode == 0


def cell_key(cfg: Config, name: str) -> bool:
    """The `key_check` of another profile that a cell runs on passed."""
    return run_quiet(shlex.split(dict(cfg.cell_runs)[name].key_check)).returncode == 0


def check_permission(cfg: Config) -> Line:
    """The permission mode of the profile in force."""
    mode = cfg.permission_mode
    if not mode:
        return OK, f"permission mode: none ({cfg.inference_profile} profile, harness {cfg.harness})"
    return OK, f"permission mode: {mode} ({cfg.inference_profile} profile)"


@dataclass(frozen=True)
class Probes:
    """What `--check` reads, each a function that a test replaces. A probe that raises gives a line with the error's type."""

    environ: Mapping[str, str]
    linear: Callable[[], object]
    github: Callable[[], object]
    log: Callable[[], str | None]  # the tail of the service's log, None when there is no log
    sessions: Callable[[], list[dict[str, Any]]]  # the host's workspaces (herdr's, or Paseo's agents): label and agent_status
    installed: Callable[[], tuple[str | None, list[str] | None]]  # the installed commit, and the commits of main under its files that it lacks
    state: Callable[[], str | None]  # the text of the state file, None when there is none
    run_started: Callable[[str], float | None]  # when an issue's newest run line was written (one Linear read), None with none
    clock: Callable[[], float] = time.time
    profile_key: Callable[[], bool] = lambda: True  # the profile's `key_check` passed
    others: tuple[tuple[str, Callable[[], object], Callable[[], object], str], ...] = ()  # each later product: its team, a Linear answer, a GitHub answer and its run_cwd
    model: Callable[[str], bool] = lambda _: True
    paseo_profile: Callable[[], Sequence[str] | None] = lambda: None
    provider: Callable[[], bool] = lambda: True
    cell_key: Callable[[str], bool] = lambda _: True  # the `key_check` of another profile that a cell runs on passed
    home_copy: Callable[[], Line | None] = lambda: None  # the agents' home copy of agent_user.py against the installed one
    visibility: Callable[[str], str] = lambda _: "private"  # a repository's visibility, `gh api repos/OWNER/NAME --jq .visibility`
    app_repos: Callable[[], Sequence[str]] = lambda: ()  # the repositories of the service's app installation (OWNER/NAME)
    paseo_endpoint: Callable[[Endpoint], None] = lambda _: None  # a cheap call to a session endpoint's daemon; raises with its error


def check_endpoints(cfgs: Sequence[Config], probe: Callable[[Endpoint], None]) -> list[Line]:
    """On Paseo, one line for each product: its session endpoint's daemon answers, or the error (a wrong password, no
    answer). Each endpoint is called once, however many products share it."""
    if not cfgs or cfgs[0].session_host != "paseo":
        return []
    found: dict[Endpoint, str | None] = {}
    lines: list[Line] = []
    for c in cfgs:
        if c.endpoint not in found:
            try:
                probe(c.endpoint)
                found[c.endpoint] = None
            except subprocess.TimeoutExpired:
                found[c.endpoint] = f"no answer within {GH_TIMEOUT} s"
            except Exception as e:
                found[c.endpoint] = str(e) or type(e).__name__
        error = found[c.endpoint]
        lines.append((OK, f"{c.team}: Paseo at {c.endpoint.name()} answers") if error is None
                     else (FAIL, f"{c.team}: Paseo at {c.endpoint.name()}: {error}"))
    return lines


def ask_endpoint(endpoint: Endpoint, run: Callable[..., subprocess.CompletedProcess[str]] = run_quiet,
                 password: Callable[[str], str] = keychain_password) -> None:
    """`paseo [--host HOST] ls -q`, with the password in the call's environment: it raises with the CLI's first line of error."""
    done = PaseoHost(run, endpoint=endpoint, password=password).call("ls", "-q")
    if done.returncode != 0:
        said = next((line.strip() for line in f"{done.stderr or ''}\n{done.stdout or ''}".splitlines() if line.strip()), "")
        raise RuntimeError(said[:200] or f"paseo exited {done.returncode}")


def run_check(cfg: Config, probes: Probes, emit: Callable[[str], None] = print) -> int:
    """Every check, one line each; 1 when a line is a FAIL."""
    now = probes.clock()
    lines: list[Line] = [*check_keys(probes.environ, team_configs(cfg)), check_answer("Linear", probes.linear),
                         check_answer("GitHub", probes.github)]
    for team, linear, github, cwd in probes.others:
        lines += [check_answer(f"Linear {team}", linear), check_answer(f"GitHub {team}", github)]
        lines.append((OK, f"{team}: run_cwd exists") if Path(cwd).expanduser().is_dir()
                     else (FAIL, f"{team}: run_cwd {cwd} is not a folder: clone the repository there"))
    lines += check_apps(team_configs(cfg), probes.visibility, probes.app_repos)
    lines.append(check_command(cfg))
    lines.append(check_token(cfg))
    lines += preflight(cfg, probes.profile_key, probes.model, probes.paseo_profile, probes.cell_key)
    lines += check_endpoints(team_configs(cfg), probes.paseo_endpoint)
    if cfg.harness == "pi":
        try:
            current = probes.provider()
        except (OSError, subprocess.SubprocessError):
            current = False
        name = cfg.inference_profile
        lines.append((OK, f"{cfg.harness} provider: {name} has no custom provider mismatch") if current else
                     (FAIL, f"{cfg.harness} provider: {name} does not match the catalog; run {ADMIN_COMMAND} to copy the current "
                            "catalog, then `agent_user.py user` to update the provider"))
    lines.append(check_review(cfg))
    lines.append(check_permission(cfg))
    try:
        state_text = probes.state()
        lines.append(check_service(probes.log(), state_text, now, cfg.poll_seconds))
        if running := check_round(state_text, now):
            lines.append(running)
    except Exception as e:
        lines.append((FAIL, f"the service: cannot read the log or the state file ({type(e).__name__})"))
    try:
        listed = probes.sessions()
        if cfg.session_host not in LISTED_HOSTS:  # a process lists nothing: only an old host's sessions are read
            listed = [s for s in listed if s.get("host") in LISTED_HOSTS]
        if cfg.session_host in LISTED_HOSTS or listed:
            lines += check_sessions(listed, now, probes.run_started, cfg.session_host)
    except Exception as e:
        if cfg.session_host in LISTED_HOSTS:
            lines.append((WARN, f"sessions: {cfg.session_host} gave no list ({type(e).__name__})"))
    try:
        lines.append(check_installed(*probes.installed()))
    except Exception as e:
        lines.append((WARN, f"installed: cannot compare with main ({type(e).__name__})"))
    try:
        if line := probes.home_copy():
            lines.append(line)
    except OSError as e:
        lines.append((WARN, f"agent_user.py: cannot compare the home copy ({type(e).__name__})"))
    for level, text in lines:
        emit(f"{level:<4} {text}")
    return 1 if any(level == FAIL for level, _ in lines) else 0


def read_text(path: Path) -> str | None:
    try:
        return path.read_text()
    except FileNotFoundError:
        return None


def append_line(path: Path, line: str) -> None:
    """One line at the end of the log, the file opened for this line only: after a rotation the next line goes to
    the new file, never to the renamed one."""
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def read_log_tail(path: Path) -> str | None:
    """The end of the log: the newest lines are all the check needs. A rotation leaves the older lines in another file."""
    try:
        with path.open("rb") as f:
            f.seek(max(0, f.seek(0, os.SEEK_END) - LOG_TAIL_BYTES))
            return f.read().decode(errors="replace")
    except FileNotFoundError:
        return None


def read_sessions(run: Run = run_quiet) -> list[dict[str, Any]]:
    done = run(["herdr", "workspace", "list"])
    if done.returncode != 0:
        raise RuntimeError("herdr workspace list failed")
    found = json.loads(done.stdout or "{}")
    spaces = found.get("result", {}).get("workspaces", []) if isinstance(found, dict) else []
    return [s for s in spaces if isinstance(s, dict)]


def read_installed(cfg: Config, run: Run = run_quiet) -> tuple[str | None, list[str] | None]:
    """The installed COMMIT and the commits of the clone's origin/main under the installed files that it lacks. A failed
    fetch leaves the clone's origin/main as it was: the comparison then reads what the last fetch saw. A COMMIT that the
    clone does not know gives None for the missing commits."""
    if not cfg.install_dir or not cfg.clone:
        raise ConfigError("[service] install_dir or clone is not set")
    try:
        commit = (Path(cfg.install_dir).expanduser() / "COMMIT").read_text().strip() or None
    except FileNotFoundError:
        return None, []
    if commit is None:
        return None, []
    clone = str(Path(cfg.clone).expanduser())
    run(["git", "-C", clone, "fetch", "-q", "origin", "main"])
    if run(["git", "-C", clone, "rev-parse", "--git-dir"]).returncode != 0:  # a broken clone is no unknown commit
        raise RuntimeError("git rev-parse failed")
    if run(["git", "-C", clone, "cat-file", "-e", f"{commit}^{{commit}}"]).returncode != 0:
        return commit, None
    done = run(["git", "-C", clone, "log", "--format=%H", f"{commit}..origin/main", "--", *INSTALLED_FILES])
    if done.returncode != 0:
        raise RuntimeError("git log failed")
    return commit, done.stdout.split()


def old_hosts_too(cfg: Config) -> Host:
    """The host in force with each old host of the state file's `hosts` record, so `--check` sees the old host's sessions
    too. Its list changes no state: the check never writes the state file."""
    return Hosts(cfg.session_host, make_host(cfg), lambda name: make_host(replace(cfg, session_host=name)),
                 read_record(Path(cfg.state_file).expanduser()))


def real_probes(cfg: Config) -> Probes:
    def github() -> None:
        done = run_quiet(["gh", "api", f"repos/{cfg.repo}", "--jq", ".id"])
        if done.returncode != 0:
            raise RuntimeError("gh api failed")

    def log() -> str | None:
        if not cfg.log_file:
            raise ConfigError("[service] log_file is not set")
        return read_log_tail(Path(cfg.log_file).expanduser())

    def started(key: str) -> float | None:
        own = next((c for c in team_configs(cfg) if key.upper().startswith(f"{c.team.upper()}-")), cfg)  # the team's own key
        [issue] = linear_api(own).issues(own.team, [], keys=[key])
        return run_started(issue.get("comments") or [])

    def later(c: Config) -> tuple[str, Callable[[], object], Callable[[], object], str]:
        def gh() -> None:
            if run_quiet(["gh", "api", f"repos/{c.repo}", "--jq", ".id"]).returncode != 0:
                raise RuntimeError("gh api failed")
        return c.team, lambda: linear_api(c).team(c.team), gh, c.run_cwd

    def visibility(repo: str) -> str:
        done = run_quiet(["gh", "api", f"repos/{repo}", "--jq", ".visibility"])
        if done.returncode != 0:
            raise RuntimeError("gh api failed")
        return done.stdout.strip()

    def installation() -> list[str]:
        """The service's gh holds the implementer app's installation token, which lists its installation's repositories."""
        done = run_quiet(["gh", "api", "--paginate", "/installation/repositories", "--jq", ".repositories[].full_name"])
        if done.returncode != 0:
            raise RuntimeError("gh api failed")
        return done.stdout.split()

    return Probes(environ=os.environ, linear=lambda: linear_api(cfg).team(cfg.team), github=github, log=log,
                  sessions=old_hosts_too(cfg).workspaces, installed=lambda: read_installed(cfg),
                  state=lambda: read_text(Path(cfg.state_file).expanduser()), run_started=started,
                  profile_key=lambda: profile_key(cfg), model=lambda model: pi_model_exists(cfg, model),
                  cell_key=lambda name: cell_key(cfg, name),
                  others=tuple(later(c) for c in team_configs(cfg)[1:]),
                  paseo_profile=lambda: read_paseo_profile(Path.home()),
                  home_copy=lambda: read_home_copy(Path.home(), agent_wrapper()), visibility=visibility,
                  app_repos=installation, paseo_endpoint=ask_endpoint,
                  provider=lambda: run_quiet(["/usr/bin/python3", "-I", str(agent_wrapper()), "provider-check",
                                                 "--profile", cfg.inference_profile]).returncode == 0)


@dataclass
class Team:
    """One product's part of a round: its config, its two reads and its runner."""

    cfg: Config
    reads: Reads
    runner: Runner


def team_round(team: Team, emit: Callable[[str], None], dry: bool, only_changes: bool, header: bool,
               token: object | None = None) -> tuple[dict[str, Any], bool]:
    """One team's read, decisions and actions, then its plan printed. Returns the team's entry for the state file (a failed round
    gives the last plan again, with the failure) and whether the round worked. It never raises. `header`: a line with the team's
    key before its lines, when the round serves several teams."""
    cfg, reads, runner = team.cfg, team.reads, team.runner
    now = runner.clock()
    stamp = _when(now)
    try:
        issues, prs = reads.issues(), reads.prs()
        plan, events = runner.plan(issues, prs, dry, reads.labels, reads.branch, reads.final, token)
    except Exception as e:  # a failed read or write must never end the process: the next round tries again
        emit(f"{stamp} round failed: {type(e).__name__}: {e}" if not header else f"{stamp} round failed ({cfg.team}): {type(e).__name__}: {e}")
        runner.shown, runner.said_at = None, now  # the next good round prints in full
        empty = {"version": PLAN_VERSION, "time": None, "poll_seconds": cfg.poll_seconds}
        return {**(runner.last or empty), "failed": {"time": stamp, "error": type(e).__name__}}, False
    runner.last = plan
    runner.confirmed_slot = None
    state = shown_state(plan)
    if not only_changes or state != runner.shown or any(e.kind not in QUIET for e in events):
        if header:
            emit(f"== {cfg.team} ==")
        for line in plan_text(plan, events, in_progress=cfg.in_progress) + event_json(plan):
            emit(line)
        runner.said_at, runner.since = now, str(plan["time"])
    elif runner.said_at is not None and now - runner.said_at >= HEARTBEAT_SECONDS:
        emit(f"{stamp} no change since {runner.since}" if not header else f"{stamp} no change since {runner.since} ({cfg.team})")
        runner.said_at = now
    runner.shown = state
    return plan, True


def slots_elsewhere(teams: Sequence[Team], me: Team) -> Callable[[], int]:
    """The count of slots that the other teams' runs held in their last plan: what `me` takes off `max_runs`. A dry round
    starts nothing, so a `would_start` or `would_start_fix` of the plan takes a slot as a real start does (a real round's
    `started` is in `building` already: it is not counted twice)."""
    def count(runner: Runner) -> int:
        plan = runner.last or {}
        held = int(plan.get("building") or 0)
        if runner.confirmed_slot and not any(s["key"] == runner.confirmed_slot for s in plan.get("slots", [])):
            held += 1
        return held + sum(1 for e in plan.get("events") or [] if e.get("event") in ("would_start", "would_start_fix"))

    return lambda: sum(count(o.runner) for o in teams if o is not me)


def combined_state(teams: Sequence[Team], entries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The state file's content: with one team, its plan as it is; with several, the first team's plan with its `team`, and
    the others' plans, each with its `team`, in `others` (`--watch` draws each apart)."""
    if len(teams) == 1:
        return dict(entries[0])
    return {**entries[0], "team": teams[0].cfg.team,
            "others": [{**e, "team": t.cfg.team} for t, e in zip(teams[1:], entries[1:], strict=True)]}


def combined_queue(teams: Sequence[Team]) -> list[str]:
    """The pinned queue's lines: each team's `queue_lines`, one after the other; with several teams, each block starts with
    the team's key before its counts line."""
    blocks = [queue_lines(t.runner.last, t.cfg.in_progress) if t.runner.last is not None else ["no plan yet"] for t in teams]
    return [*halt_banner({"halted": teams[0].runner.halt.text()}),
            *(f"{t.cfg.team}: {line}" if i == 0 and len(teams) > 1 else line
              for t, block in zip(teams, blocks, strict=True) for i, line in enumerate(block))]


def round_all(teams: Sequence[Team], emit: Callable[[str], None] = print, dry: bool = True, state_file: Path | None = None,
              only_changes: bool = False, daemon: PaseoDaemon | None = None) -> bool:
    """One round over every team, one after the other: read, decide, act (or print), then print each plan, keep the one
    queue and write the state file. At its start it writes the round's start into the state file beside the last plan
    (`mark_round_start`). With `only_changes` (the loop), a team prints only when it has an event that is
    not a dry run's repeat, or when `shown_state` changed; after `HEARTBEAT_SECONDS` of quiet it prints one `no change since`
    line. A failed read or write prints its error each time, writes the last plan again with the failure to `state_file`
    (`--check` and `--watch` read it), and returns False; it never raises. One team's failure never stops the others. The
    teams share `max_runs`: each runner counts the slots that the others held in their last plan (`Runner.elsewhere`). With
    `daemon` (Paseo, not dry), `daemon_step` keeps that daemon on the profile in force before any start."""
    first = teams[0]
    halt, now = first.runner.halt, first.runner.clock()
    if state_file is not None:
        try:
            mark_round_start(state_file, now)  # a slow round shows in `--watch` and `--check` before it ends
        except OSError:
            pass  # the write at the round's end reports an unwritable state file
    if not dry and isinstance(primary := primary_of(first.runner.host), (PaseoHost, PaseoEndpoints)):
        for line in primary.clean_failed_starts():
            emit(line)
    resumed, confirmed, halt.narrowed = None, False, None
    resumed = halt.pause_step(first.cfg, now)
    if not dry:
        for team in teams:
            if confirm := team.runner.confirm_launch:
                try:
                    events = confirm()
                except Exception as e:
                    if halt.confirmation_error is not type(e):
                        emit(f"session confirmation failed: {type(e).__name__}")
                        halt.confirmation_error = type(e)
                    if not (halt.cause or "").endswith(CONFIRM_ACTION):
                        # A cause of None (the owner's resume cleared the pause while the confirmation still fails)
                        # enters again: the starts wait until the host answers.
                        halt.enter(first.cfg, now, (halt.cause or "session not confirmed") + CONFIRM_ACTION, halt.scope)
                else:
                    halt.confirmation_error = None
                    if halt.cause:
                        halt.cause = halt.cause.removesuffix(CONFIRM_ACTION)
                    for event in events:
                        emit(event.text)
                    if events:
                        team.runner.confirmed_slot = events[0].key
                        confirmed = True
        if not confirmed and not any(t.runner.confirm_launch for t in teams):
            resumed = halt.recover(first.cfg, now) or resumed
        if daemon is not None:
            resumed = daemon_step(first.cfg, halt, daemon, now, emit) or resumed
    if resumed or confirmed:
        for t in teams:
            t.runner.resumed_at = now
    elif halt.narrowed is not None:
        for t in teams:  # a profile that the narrowing released counts its fix waits from now, as after a full resume
            for name in halt.released:
                if not halt.blocks(name):
                    t.runner.stops.resumed[name] = now
    token = object()  # one round of every team: the shared stop is read once
    results = [team_round(t, emit, dry, only_changes, len(teams) > 1, token) for t in teams]
    halt.retry_fix = None
    if confirmed:
        # Count the confirmed session in its team's plan before any other team can start.
        halt.failed_fix = None
        # The owner's pause stays, and so does the daemon's switch
        resumed = None if halt.owner_hold or halt.daemon_hold else halt.resume(now, "host confirmed the session")
    if resumed and halt.cause is None:
        emit(resumed)
        halt.shown = None
    if (shown := halt.text()) and halt.shown != shown:
        emit(f"halted: {shown}")
        halt.shown = shown
    ok = all(good for _, good in results)
    if not dry and halt.pending and first.runner.writes and (notice := first.runner.writes.notice):
        try:
            halt.notify(notice, now)
        except Exception as e:
            emit(f"setup notice failed: {type(e).__name__}")
    queue = first.runner.queue
    # After a halt, the queue must show or clear the banner even if a team has no plan yet.
    if queue is not None and (halt.next_check > 0 or all(t.runner.last for t in teams)):
        for line in queue.put(combined_queue(teams), str((first.runner.last or {}).get("time") or _when(now)), now):
            emit(line)
    if state_file is not None:
        try:
            state = combined_state(teams, [entry for entry, _ in results])
            if halt.cause:
                state["halted"] = halt.text()
            if isinstance(first.runner.host, Hosts):
                state["hosts"] = first.runner.host.record()  # the next process reads it: a switch of host needs no wait
            write_state(state_file, state)
        except OSError as e:  # the plan printed; the next round writes the file again
            emit(f"state file not written: {type(e).__name__}")
            return False
    return ok


def round_once(cfg: Config, reads: Reads, emit: Callable[[str], None] = print, runner: Runner | None = None,
               dry: bool = True, state_file: Path | None = None, only_changes: bool = False) -> bool:
    """One round of one team: `round_all` with the one team."""
    runner = runner or Runner(cfg, ProcessHost(Path(cfg.log_dir).expanduser()))  # nothing starts in a dry run
    return round_all([Team(cfg, reads, runner)], emit, dry, state_file, only_changes)


def main(argv: Sequence[str] | None = None, reads: Reads | Mapping[str, Reads] | None = None, sleep: Callable[[float], None] = time.sleep,
         host: Host | None = None, writes: Writes | None = None, probes: Probes | None = None,
         queue: PinnedComment | None = None, daemon: PaseoDaemon | None = None) -> int:
    """The process: `--once` runs one round; without it, rounds go on with `poll_seconds` between them."""
    parser = argparse.ArgumentParser(description="Poll Linear and GitHub, start one session for a fix that no session took and escalate it when that one takes it neither, print the rest.")
    parser.add_argument("--config", type=Path, default=Path("orchestrator/orchestrator.toml"))
    parser.add_argument("--once", action="store_true", help="run one round and stop")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the decisions and write nothing to Linear or GitHub, whatever the config says")
    parser.add_argument("--check", action="store_true", help="check the service's health, one line each; exit 1 on a FAIL")
    parser.add_argument("--preflight", action="store_true", help="check the selected profile without starting any sessions")
    parser.add_argument("--watch", action="store_true", help="draw the plan of state_file in place each time it changes; Ctrl-C ends it")
    parser.add_argument("--log", type=Path, metavar="FILE",
                        help="append each line to FILE, opened again for each line, so a rotation loses none")
    args = parser.parse_args(argv)
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        print(f"orchestrator: {e}", file=sys.stderr)
        return 2
    if args.check:
        return run_check(cfg, probes or real_probes(cfg))
    if args.watch:
        def terminal_size() -> tuple[int, int]:
            size = shutil.get_terminal_size()
            return size.columns, size.lines

        def write(text: str) -> None:
            sys.stdout.write(text)
            sys.stdout.flush()

        return watch(Path(cfg.state_file).expanduser(), write, terminal_size,
                     sys.stdout.isatty() and not os.environ.get("NO_COLOR"), sleep)
    def setup() -> list[Check]:
        return preflight(cfg, probes.profile_key if probes else lambda: profile_key(cfg),
                         probes.model if probes else lambda model: pi_model_exists(cfg, model),
                         probes.paseo_profile if probes else lambda: read_paseo_profile(Path.home()),
                         probes.cell_key if probes else lambda name: cell_key(cfg, name))

    if args.preflight:
        lines = setup()
        for level, text in lines:
            print(f"{level:<4} {text}")
        return int(any(level == FAIL for level, _ in lines))
    dry = cfg.dry_run or args.dry_run
    cfgs = team_configs(cfg)
    state_file = Path(cfg.state_file).expanduser()
    primary = host or make_host(cfg)  # one host for every team: its list shows every team's sessions
    # the old hosts of the last process's record keep their sessions' wakes and closes
    the_host = Hosts(cfg.session_host, primary, lambda name: make_host(replace(cfg, session_host=name)), read_record(state_file))
    def runtime_setup() -> list[Line]:
        return [*setup(), check_answer(f"{cfg.session_host} session list", the_host.workspaces)]

    halt = Halt(check=runtime_setup, hold=lambda: pause_path(args.config).exists())
    if not dry and not args.once:
        # An automatic service restart may not have gone through the installer's preflight.
        if failed := failed_checks(runtime_setup()):
            halt.enter_failed(cfg, time.time(), failed)
        else:
            halt.pending = True  # close any notice left open by the last service process
    teams: list[Team] = []
    stops = Stops()  # one stop and retry state for every team: they share one host
    for c in cfgs:
        own = reads.get(c.team) if isinstance(reads, Mapping) else reads if c is cfg else None
        if own is None and reads is not None:
            print(f"orchestrator: no reads for team {c.team}", file=sys.stderr)
            return 2
        teams.append(Team(c, own or real_reads(c), Runner(c, the_host, None if dry else (writes or real_writes(c)),
                                                       halt=halt, prestart=setup, stops=stops,
                                                       queue=(queue if queue is not None else make_queue(c, dry)) if c is cfg else None)))
    for t in teams:
        t.runner.elsewhere = slots_elsewhere(teams, t)
    log: Path | None = args.log

    def emit(line: str) -> None:
        if log is None:
            print(line, flush=True)
        else:
            try:
                append_line(log, line)
            except OSError as e:  # never a crash loop for a log: the line goes to standard output, the service's crash file
                print(f"log not written ({type(e).__name__}): {line}", flush=True)

    # The service keeps only the local daemon: a product whose `paseo_host` names another daemon has that daemon kept by its own user
    if daemon is None and host is None and not dry and (local := local_paseo(primary)) is not None:  # a test's own host gets no real daemon
        daemon = PaseoDaemon(cfg.inference_profile, daemon_profiles(cfg), local, hide={c.linear_key_env for c in cfgs},
                             review_file=Path(__file__).resolve().parent / "local-review.json")
    try:
        while True:
            ok = round_all(teams, emit, dry=dry, state_file=state_file, only_changes=not args.once, daemon=daemon)
            if args.once:
                return 0 if ok else 1
            sleep(cfg.poll_seconds)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
