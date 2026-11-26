"""The agents' macOS user (.workflow/agent_user.py): `admin` makes the group, the user and a closed home and runs
again with no change; `check` fails on each open guard; the keys reach the Keychain only on `security -i`'s standard input;
the app's token is made from a JWT that openssl signs and waits in the Keychain until it nearly ends. Every command the
script runs goes through a fake: no test touches the system's users or Keychain."""

import base64
import calendar
import importlib.util
import json
import os
import plistlib
import pty
import re
import select
import subprocess
import sys
import threading
import time
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("agent_user", REPO / ".workflow" / "agent_user.py")
assert SPEC and SPEC.loader
agent = importlib.util.module_from_spec(SPEC)
sys.modules["agent_user"] = agent
# The tests' fixtures name the user `acme-agent`: pin it, so a checkout or an account with another name runs them too
_CALLER_AGENT_USER = os.environ.get("AGENT_USER")
os.environ["AGENT_USER"] = "acme-agent"
SPEC.loader.exec_module(agent)
if _CALLER_AGENT_USER is None:
    del os.environ["AGENT_USER"]
else:
    os.environ["AGENT_USER"] = _CALLER_AGENT_USER
REAL_REVIEW_FILE = agent.review_file  # before the autouse fixture replaces it: a test that reads a file of its own
REAL_PROFILE_IN_FORCE = agent.profile_in_force  # the same for the profile in force

MEMBER, NOT_MEMBER = "user is a member of the group", "user is not a member of the group"
# The local review's model and effort level of each profile: the orchestrator's config names them, as its example does
# and `install`/`restart` write them beside the installed copy of the script.
REVIEW = {name: (table["review_model"], table["review_effort"])
          for name, table in tomllib.loads((REPO / "orchestrator" / "orchestrator.example.toml").read_text())["profiles"].items()}


@pytest.fixture(autouse=True)
def review_file(tmp_path_factory, monkeypatch):
    """The local review's settings: `owlcode install` (or `restart`) writes each profile's model and
    effort level, which the config names, as JSON beside the installed copy, and the script reads that file. A test writes
    its own to read, so none reads this machine's."""
    path = tmp_path_factory.mktemp("review") / agent.REVIEW_FILE
    path.write_text(json.dumps({name: {"model": model, "effort": effort} for name, (model, effort) in REVIEW.items()}))
    monkeypatch.setattr(agent, "review_file", lambda: path)
    return path


@pytest.fixture(autouse=True)
def in_force(monkeypatch):
    """The profile in force: `install` and `restart` write the config's `inference_profile` beside the installed
    copy, and `paseo-daemon` reads it. A test sets its own in the box, so none reads this machine's."""
    box = {"name": agent.DEFAULT_PROFILE}
    monkeypatch.setattr(agent, "profile_in_force", lambda: box["name"])
    return box


@pytest.fixture(autouse=True)
def no_paseo_app(tmp_path_factory, monkeypatch):
    """The Mac's own Paseo app never reaches a test: a test that wants one makes it."""
    monkeypatch.setattr(agent, "PASEO_APP", tmp_path_factory.mktemp("no-app") / "paseo")


class FakeSh(agent.Sh):
    """Replies by the longest matching command prefix (0 and no output when none matches); every call is recorded."""

    def __init__(self, replies: dict[tuple[str, ...], tuple[int, str]]) -> None:
        self.replies = replies
        self.calls: list[list[str]] = []
        self.stdins: list[str | None] = []
        self.cwds: list[Path | None] = []
        self.out_cwds: dict[tuple[str, ...], Path | None] = {}

    def reply(self, cmd) -> tuple[int, str]:
        for prefix in sorted(self.replies, key=len, reverse=True):
            if tuple(cmd[: len(prefix)]) == prefix:
                return self.replies[prefix]
        return 0, ""

    def out(self, cmd, stdin=None, cwd=None):
        self.calls.append(list(cmd))
        self.stdins.append(stdin)
        self.out_cwds[tuple(cmd)] = cwd
        code, text = self.reply(cmd)
        return subprocess.CompletedProcess(list(cmd), code, text, "")

    def tty(self, cmd, cwd=None, env=None):
        self.calls.append(list(cmd))
        self.stdins.append(None)
        self.cwds.append(cwd)
        return self.reply(cmd)[0]


def test_model_catalog_shape():
    catalog = agent.CATALOG
    assert set(catalog) == {"harnesses", "profiles", "providers", "efforts", "pi_compat"}
    for route in catalog["profiles"].values():
        assert route["harness"] in catalog["providers"][route["provider"]]["connections"]
    for provider in catalog["providers"].values():
        assert provider["privacy"]["url"].startswith("https://")
        assert provider["privacy"]["zdr"] in (True, False, None)
        for name, connection in provider["connections"].items():
            harness = catalog["harnesses"][name]
            assert connection["api"] in harness["apis"]
            assert bool(connection["permissions"]) == bool(harness["permission_flag"])
            assert connection["base_url"].startswith("https://")
            assert bool(connection["key_variable"]) == bool(provider["keychain"])
        for model in provider["models"].values():
            for limit in ("context_window", "max_output"):
                assert model[limit] is None or (isinstance(model[limit], int) and model[limit] > 0)
            for name, levels in model["efforts"].items():
                assert name in provider["connections"]
                assert any(value is not None for value in catalog["efforts"][levels].values())
            if prices := model.get("prices"):
                assert {"input", "cached_input", "output"} <= prices.keys()
                source = provider["price_source"]
            else:
                source = provider["subscription"]
                assert source["limits"]
            assert source["url"].startswith("https://")
            assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", source["read"])


def test_model_table_lists_valid_efforts_and_price_variants():
    lines = agent.model_table().splitlines()
    assert re.split(r" {2,}", lines[0]) == ["Provider", "Model ID", "Harnesses", "Effort levels", "Tier", "USD/M input/cached/output"]
    rows = [re.split(r" {2,}", line) for line in lines[1:]]
    models = {(provider, model): (harnesses, levels, tier, price) for provider, model, harnesses, levels, tier, price in rows}
    assert len(models) == len(rows)
    assert models["lithosai", "deepseek-ai/DeepSeek-V4.1-Flash"] == (
        "pi", "low, high, max", "Base", "$0.15/$0.003/$1.2; promo output $0.6")
    assert models["lithosai", "zai-org/GLM-5.3"][:2] == ("pi", "low, high, max")
    assert models["ollama", "deepseek-v4.1-flash"] == ("pi", "medium, high", "-", "$0.3/$0.006/$1.2; off-peak $0.15/$0.003/$0.6")
    assert models["anthropic", "claude-sonnet-5-5"] == ("claude", "low, medium, high, xhigh, max", "-", "subscription")
    assert models["openai-codex", "gpt-6.2-sol"][-1] == "subscription"
    assert ("lithosai", "moonshotai/Kimi-K3.1-Pro-ultra") not in models


def test_model_table_combines_harnesses_only_when_their_efforts_match(monkeypatch):
    efforts = {"shared": {"low": "low", "high": "high"}, "other": {"low": None, "high": "max"}}
    models = {"same": {"efforts": {"claude": "shared", "pi": "shared"}},
              "different": {"efforts": {"claude": "shared", "pi": "other"}}}
    monkeypatch.setattr(agent, "CATALOG", {"efforts": efforts, "providers": {"vendor": {"models": models}}})
    rows = [re.split(r" {2,}", line)[:4] for line in agent.model_table().splitlines()[1:]]
    assert rows == [["vendor", "same", "claude, pi", "low, high"],
                    ["vendor", "different", "claude", "low, high"],
                    ["vendor", "different", "pi", "high→max"]]


@pytest.mark.parametrize("status", (0, 1))
def test_check_model_lists_reports_drift_without_printing_keys(status, capsys):
    providers = agent.CATALOG["providers"]
    replies = keychain({"ollama-key": "ollama-secret", "lithosai-key": "lithosai-secret"})
    for name in ("ollama", "lithosai"):
        provider = providers[name]
        endpoint = provider["model_list"]
        listed = list(provider["models"]) + ["added-model"]
        if status and name == "ollama":
            listed.remove("glm-5.3")
        cmd = ("/usr/bin/curl", "--disable", "--silent", "--fail", "--max-time", "20", "-H", "@-", endpoint["url"])
        replies[cmd] = (0, json.dumps({endpoint["items"]: [{endpoint["id"]: model} for model in listed]}))
    sh = FakeSh(replies)
    assert agent.check_model_lists(sh) == status
    output = capsys.readouterr().out
    assert output.count("listed IDs not in catalog: added-model") == 2
    assert ("catalog IDs not listed: glm-5.3" in output) == bool(status)
    for cmd, stdin in zip(sh.calls, sh.stdins, strict=True):
        assert all("-secret" not in part for part in cmd)
        if cmd[0] == "/usr/bin/curl":
            name = "ollama" if "ollama.com" in cmd[-1] else "lithosai"
            assert stdin == f"Authorization: Bearer {name}-secret\n"
    assert "-secret" not in output


@pytest.mark.parametrize(("key", "reply", "error"), [
    ("", None, "no Keychain key"),
    ("invalid\nheader", None, "invalid Keychain key"),
    ("key", (22, ""), "could not read its model list"),
    ("key", (0, "{broken"), "could not read its model list"),
    ("key", None, "could not read its model list"),
])
def test_model_list_errors_do_not_stop_the_next_provider(key, reply, error, capsys):
    class ListReplies(FakeSh):
        def out(self, cmd, stdin=None, cwd=None):
            if cmd[-1] == "https://ollama.com/api/tags":
                if reply is None:
                    raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
                code, text = reply
                return subprocess.CompletedProcess(cmd, code, text, "")
            return super().out(cmd, stdin, cwd)

    sh = ListReplies(keychain({"ollama-key": key, "lithosai-key": "other-key"}) | {("/usr/bin/curl",): (0, '{"data": []}')})
    assert agent.check_model_lists(sh) == 1
    output = capsys.readouterr().out
    assert f"ollama: {error}" in output
    assert "lithosai: catalog IDs not listed:" in output


class NoAsk(agent.Ask):
    def plain(self, prompt):
        raise AssertionError(f"asked: {prompt}")

    secret = plain


def folder(path: Path, mode: int) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(mode)
    return path


def membership(admin=False, agents=False, staff=False) -> dict[tuple[str, ...], tuple[int, str]]:
    def says(yes):
        return 0, MEMBER if yes else NOT_MEMBER
    base = ("dsmemberutil", "checkmembership", "-U", "acme-agent", "-G")
    return {(*base, "admin"): says(admin), (*base, "agents"): says(agents), (*base, "staff"): says(staff)}


def test_admin_on_a_new_machine_makes_the_group_the_user_and_a_closed_home(tmp_path):
    owner_home = folder(tmp_path / "owner", 0o750)  # macOS's default: open to the group staff
    home = tmp_path / "acme-agent"
    sh = FakeSh({("dscl", ".", "-read", "/Groups/agents"): (56, ""), ("dscl", ".", "-read", "/Users/acme-agent"): (56, ""),
                 ("sudo", "-u", "owner", "git", "config", "--global", "user.name"): (0, "Mauro Lopes\n"),
                 ("sudo", "-u", "owner", "git", "config", "--global", "user.email"): (0, "owner@example.com\n"),
                 ("sudo", "-u", "owner", "-H", "test", "-f"): (1, ""),
                 ("sudo", "-u", "owner", "-H", "cat"): (0, "ssh-ed25519 AAAA acme-agent-agent\n"),
                 ("launchctl", "print-disabled", "system"): (0, '\t"com.openssh.sshd" => disabled\n'),
                 **membership(staff=True)})
    sshd_dir = folder(tmp_path / "sshd_config.d", 0o755)
    notes = agent.admin(sh, "owner", owner_home, home, "the script's text", sshd_dir, ask=Says("y"))
    calls = sh.calls
    create = ["dseditgroup", "-o", "create", "-i", "600", "-r", "Coding agents", "agents"]
    add = ["sysadminctl", "-addUser", "acme-agent", "-fullName", "acme-agent agent", "-password", "-"]
    assert calls.index(create) < calls.index(add), "the group first: the user's primary group is set to it"
    assert "-admin" not in add, "a Standard user"
    for cmd in (["dscl", ".", "-create", "/Users/acme-agent", "PrimaryGroupID", "600"],
                ["dseditgroup", "-o", "edit", "-a", "acme-agent", "-t", "user", "agents"],
                ["dseditgroup", "-o", "edit", "-d", "acme-agent", "-t", "user", "staff"],
                ["dscl", ".", "-create", "/Users/acme-agent", "IsHidden", "1"],
                ["createhomedir", "-c", "-u", "acme-agent"]):
        assert cmd in calls, cmd
    chown, chmod = ["chown", "-R", "acme-agent:agents", str(home)], ["chmod", "700", str(home)]
    as_agent = ["sudo", "-u", "acme-agent", "-H"]
    copy = [*as_agent, "/usr/bin/tee", str(home / "agent_user.py")]
    name = [*as_agent, "git", "-C", str(home), "config", "--file", str(home / ".gitconfig"), "user.name", "Mauro Lopes"]
    email = [*as_agent, "git", "-C", str(home), "config", "--file", str(home / ".gitconfig"), "user.email", "owner@example.com"]
    assert calls.index(chown) < calls.index(chmod) < calls.index(copy), "the home is the agent's and closed before a file lands"
    assert name in calls and email in calls
    assert sh.stdins[calls.index(copy)] == "the script's text"
    assert_root_writes_no_file_in(home, calls)
    assert not any(str(owner_home) in " ".join(c) and c[0] == "chmod" for c in calls), "the owner's home is the owner's"
    assert any("chmod 700 ~" in n for n in notes) and any("user" in n for n in notes)
    # Sshd's rules, the owner's key made as the owner, the agent's key line written as the agent
    conf = (sshd_dir / "000-acme-agent.conf").read_text()
    assert "AllowStreamLocalForwarding local" in conf and "AllowTcpForwarding local" in conf
    assert ("AllowUsers acme-agent@127.0.0.1 acme-agent@::1 acme-agent@100.64.0.0/10 acme-agent@fd7a:115c:a1e0::/48"
            in conf), "This Mac and the tailnet, never the home LAN"
    assert calls.index(["sshd", "-t"]) < calls.index([*as_agent, "/usr/bin/tee", str(home / ".ssh" / "authorized_keys")])
    key = str(owner_home / ".ssh" / "acme-agent-agent")
    assert ["sudo", "-u", "owner", "-H", "ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "acme-agent-agent", "-f", key] in calls
    keys = calls.index([*as_agent, "/usr/bin/tee", str(home / ".ssh" / "authorized_keys")])
    assert sh.stdins[keys] == 'from="127.0.0.1,::1",restrict,pty ssh-ed25519 AAAA acme-agent-agent\n'
    assert ["dseditgroup", "-o", "edit", "-a", "agents", "-t", "group", "com.apple.access_ssh"] in calls
    assert any("ForwardAgent no" in n and "Host acme-agent" in n for n in notes), "the owner pastes the block: never edited"
    assert any("Remote Login is off" in n for n in notes) and any("herdr machine add acme-agent" in n for n in notes)


def test_admin_runs_again_with_no_change(tmp_path):
    owner_home = folder(tmp_path / "owner", 0o700)
    home = folder(tmp_path / "acme-agent", 0o700)
    sh = FakeSh({**membership(agents=True), ("dscl", ".", "-read", "/Groups/com.apple.access_ssh"): (56, ""),
                 ("sudo", "-u", "owner", "-H", "cat"): (0, "ssh-ed25519 AAAA acme-agent-agent\n")})
    sshd_dir = folder(tmp_path / "sshd_config.d", 0o755)
    (sshd_dir / "000-acme-agent.conf").write_text(agent.sshd_conf())
    notes = agent.admin(sh, "owner", owner_home, home, "the script's text", sshd_dir)
    made = [c for c in sh.calls if c[0] in ("dseditgroup", "sysadminctl", "createhomedir", "sshd")
            or "ssh-keygen" in c]
    assert made == [] and len(notes) == 3, (made, notes)  # the Host block, herdr's command, the next step
    assert ["chown", "-h", "acme-agent:agents", str(home)] in sh.calls, "the folder itself: its files may be the agent's symlinks"
    assert not any(c[:2] == ["chown", "-R"] for c in sh.calls)
    assert_root_writes_no_file_in(home, sh.calls)


def assert_root_writes_no_file_in(home: Path, calls: list[list[str]], user: str = "acme-agent") -> None:
    """A command that root runs names no path inside the agent's home but the home itself, so a symlink
    that the agent planted there never makes root write outside it."""
    for cmd in calls:
        if cmd[:3] == ["sudo", "-u", user]:
            continue
        inside = [a for a in cmd if a.startswith(str(home) + "/")]
        assert not inside, cmd


def test_admin_puts_the_old_sshd_rules_back_when_sshd_refuses_the_new(tmp_path):
    """A broken sshd_config would refuse every login: `sshd -t` checks the new file, and a refusal leaves the old one."""
    owner_home = folder(tmp_path / "owner", 0o700)
    home = folder(tmp_path / "acme-agent", 0o700)
    sshd_dir = folder(tmp_path / "sshd_config.d", 0o755)
    (sshd_dir / "000-acme-agent.conf").write_text("# the old rules\n")
    with pytest.raises(agent.Refused, match="no hostkeys available"):  # sshd's own reason
        agent.admin(FakeSh({**membership(agents=True), ("sshd", "-t"): (255, "sshd: no hostkeys available -- exiting.\n")}),
                    "owner", owner_home, home, "x", sshd_dir)
    assert (sshd_dir / "000-acme-agent.conf").read_text() == "# the old rules\n"
    (sshd_dir / "000-acme-agent.conf").unlink()
    with pytest.raises(agent.Refused):
        agent.admin(FakeSh({**membership(agents=True), ("sshd", "-t"): (255, "")}), "owner", owner_home, home, "x", sshd_dir)
    assert not (sshd_dir / "000-acme-agent.conf").exists(), "no file when there was none"


def test_admin_refuses_a_taken_gid_and_an_admin_user(tmp_path):
    owner_home = folder(tmp_path / "owner", 0o700)
    taken = FakeSh({("dscl", ".", "-read", "/Groups/agents"): (56, ""),
                    ("dscl", ".", "-search", "/Groups", "PrimaryGroupID", "600"): (0, "builders\t\tPrimaryGroupID = (600)\n")})
    with pytest.raises(agent.Refused, match="builders"):
        agent.admin(taken, "owner", owner_home, tmp_path / "h", "x", tmp_path)
    assert not any(c[:3] == ["dseditgroup", "-o", "create"] for c in taken.calls)
    with pytest.raises(agent.Refused, match="admin"):
        agent.admin(FakeSh(membership(admin=True)), "owner", owner_home, tmp_path / "h", "x", tmp_path)


GH = ("sudo", "-u", "acme-agent", "-H", "/usr/bin/python3", "-I")


SSHD_T = ("sshd", "-T", "-C", "user=acme-agent,host=localhost,addr=127.0.0.1")
SSH_G = ("sudo", "-u", "owner", "-H", "ssh", "-G", "acme-agent")
# real sshd -T prints a list one value a line
SSHD_OK = "".join(f"{k} {v}\n" for k, v in agent.SSHD_WANTS.items()) + "".join(f"allowusers {a}\n" for a in agent.ALLOWED)
SSH_G_OK = "hostname 127.0.0.1\nuser acme-agent\nforwardagent no\nport 22\n"


def herdr_machine(home: Path) -> None:
    """What `admin` and herdr leave for the herdr machine: the agent's key line, herdr's own install."""
    if home.is_dir():
        folder(home / ".ssh", 0o700)
        (home / ".ssh" / "authorized_keys").write_text(f"{agent.KEY_OPTIONS} ssh-ed25519 AAAA acme-agent-agent\n")
        folder(home / ".local" / "share" / "mise" / "shims", 0o700)
        folder(home / ".local" / "bin", 0o700)
        (home / ".local" / "bin" / "herdr").write_text("#!/bin/sh\n")
        (home / ".local" / "bin" / "herdr").chmod(0o755)


# the agents' clone's AGENTS.md: its table "The teams" names the loop's repositories; a table of another section does not
TEAMS_MD = """# Acme

## The teams

| Team | Repository | The owner's checkout | The Linear key |
|---|---|---|---|
| ABC | maurolopes/acme-agent | `~/dev/acme-agent` | `LINEAR_API_KEY` |
| XYZ | maurolopes/acme-loop | `~/dev/acme-loop` | `LINEAR_API_KEY` |
| DEF | maurolopes/acme-tools | `~/dev/acme-tools` | `LINEAR_API_KEY` |

## Rules

| QQQ | maurolopes/not-a-team | x | y |
"""
LOOP = ["maurolopes/acme-agent", "maurolopes/acme-loop", "maurolopes/acme-tools"]
CAT = ("sudo", "-u", "acme-agent", "-H", "/bin/cat", "--")


def test_the_loop_s_repositories_are_the_agents_repository_and_the_rows_of_the_teams():
    assert agent.team_repos(TEAMS_MD) == LOOP
    assert agent.loop_repos(TEAMS_MD) == tuple(LOOP)
    assert agent.loop_repos("") == (agent.REPO,), "no table: the agents' repository only"
    assert agent.team_repos("## The teams\n\n| Team | Repository |\n|---|---|\n") == [], "the header names no repository"


def guards_ok(home: Path) -> dict[tuple[str, ...], tuple[int, str]]:
    gh = (*GH, str(home / "agent_user.py"), "gh", "api")
    herdr_machine(home)
    return {**membership(agents=True), ("id", "-gn", "acme-agent"): (0, "agents\n"),
            ("sudo", "-u", "acme-agent", "ls"): (1, ""),
            (*gh, "/installation/repositories"): (0, json.dumps(LOOP)), (*gh, "/user"): (1, ""), CAT: (0, TEAMS_MD),
            SSHD_T: (0, SSHD_OK), SSH_G: (0, SSH_G_OK)}


def test_check_passes_when_every_guard_holds(tmp_path):
    home = folder(tmp_path / "acme-agent", 0o700)
    sh = FakeSh(guards_ok(home))
    lines = agent.check(sh, "owner", folder(tmp_path / "owner", 0o700), home)
    assert [line.status for line in lines] == ["ok"] * len(lines) and len(lines) == 16, lines
    as_the_app = {cwd for cmd, cwd in sh.out_cwds.items() if "gh" in cmd}
    assert as_the_app == {home}, "The agent's gh runs in a folder the agent can read, not in the owner's checkout"


def test_check_fails_on_each_open_guard(tmp_path):
    home = folder(tmp_path / "acme-agent", 0o750)
    folder(home / ".ssh", 0o700)
    (home / ".ssh" / "id_ed25519").write_text("-----BEGIN OPENSSH PRIVATE KEY-----\n")
    (home / ".ssh" / "id_ed25519.pub").write_text("ssh-ed25519 AAAA\n")
    folder(home / "Library" / "Application Support" / "Bitwarden CLI", 0o700)
    gh = (*GH, str(home / "agent_user.py"), "gh", "api")
    replies = {**guards_ok(home), **membership(admin=True, agents=True, staff=True), ("id", "-gn", "acme-agent"): (0, "staff\n"),
               ("sudo", "-u", "acme-agent", "ls"): (0, ""),
               (*gh, "/installation/repositories"): (0, '["maurolopes/acme-agent", "maurolopes/other"]'),
               (*gh, "/user"): (0, "{}")}
    lines = agent.check(FakeSh(replies), "owner", folder(tmp_path / "owner", 0o750), home)
    failed = {line.name for line in lines if line.status == "FAIL"}
    assert failed == {"not an admin", "not in staff", "primary group", "owner's home 700", "agent's home 700",
                      "owner's home closed to the agent", "no SSH key", "no Bitwarden", "the app sees the loop's repositories only",
                      "gh is the app, not a person"}, lines
    assert next(line for line in lines if line.name == "primary group").detail == "staff, expected agents"
    assert next(line for line in lines if line.name == "no SSH key").detail.endswith("id_ed25519 (an agent must not reach the server)")


def test_check_fails_on_each_open_door_of_the_herdr_machine(tmp_path):
    """A password, a forwarding, a key without restrict, the owner's agent forwarded, herdr a link to another
    user's file, or a herdr in the agent's mise."""
    home = folder(tmp_path / "acme-agent", 0o700)
    replies = guards_ok(home)
    (home / ".ssh" / "authorized_keys").write_text("ssh-ed25519 AAAA acme-agent-agent\n")
    herdr = home / ".local" / "bin" / "herdr"
    herdr.unlink()
    herdr.symlink_to("/opt/homebrew/bin/herdr")
    (home / ".local" / "share" / "mise" / "shims" / "herdr").symlink_to(home / ".local" / "bin" / "mise")
    open_doors = SSHD_OK.replace("passwordauthentication no", "passwordauthentication yes").replace(
        "allowtcpforwarding local", "allowtcpforwarding yes")
    replies |= {SSHD_T: (0, open_doors), SSH_G: (0, "hostname 127.0.0.1\nuser acme-agent\nforwardagent yes\n")}
    lines = agent.check(FakeSh(replies), "owner", folder(tmp_path / "owner", 0o700), home)
    failed = {line.name: line.detail for line in lines if line.status == "FAIL"}
    assert set(failed) == {"sshd: the agent only, with a key, no listening or agent forwarding", "the agent's keys: herdr's, and the phones you approved",
                           "your ssh config: no agent forwarding", "herdr is the agent's own file", "no herdr in the agent's mise"}, lines
    assert "passwordauthentication yes" in failed["sshd: the agent only, with a key, no listening or agent forwarding"]
    assert "allowtcpforwarding yes" in failed["sshd: the agent only, with a key, no listening or agent forwarding"]


def test_check_gives_its_lines_when_admin_stopped_before_the_home(tmp_path):
    """`sysadminctl` makes the user with no home, and admin makes it later."""
    home = tmp_path / "acme-agent"
    sh = FakeSh(guards_ok(home))
    lines = agent.check(sh, "owner", folder(tmp_path / "owner", 0o700), home)
    assert [line.name for line in lines if line.status == "FAIL"] == ["agent's home 700", "the agent's keys: herdr's, and the phones you approved",
                                                                     "herdr is the agent's own file"], lines
    assert all(cwd is None or cwd.is_dir() for cwd in sh.out_cwds.values()), sh.out_cwds


def test_check_says_when_there_is_no_user_or_no_token(tmp_path):
    lines = agent.check(FakeSh({("dscl", ".", "-read", "/Users/acme-agent"): (56, "")}), "owner", tmp_path, tmp_path)
    assert [(line.status, line.name) for line in lines] == [("FAIL", "user")]
    home = folder(tmp_path / "acme-agent", 0o700)
    said = "Traceback (most recent call last):\n  ...\nagent_user.py: gh is not installed for acme-agent: run `user` again\n"
    replies = {**guards_ok(home), (*GH, str(home / "agent_user.py"), "gh", "api", "/installation/repositories"): (1, said)}
    lines = agent.check(FakeSh(replies), "owner", folder(tmp_path / "owner", 0o700), home)
    assert [line.name for line in lines if line.status == "FAIL"] == ["app token"]
    assert next(line for line in lines if line.name == "app token").detail.endswith("gh is not installed for acme-agent: run `user` again"), "The error's own line"


def test_gh_is_the_agent_s_own_through_its_mise_when_path_has_none(tmp_path, monkeypatch):
    """`check` runs the script with sudo's PATH, and the agent's gh lives in its mise, never in Homebrew."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", f"{tmp_path / '.local' / 'bin'}:/nowhere")
    mise = str(tmp_path / ".local" / "bin" / "mise")
    gh = tmp_path / ".local" / "share" / "mise" / "installs" / "github-cli" / "2.96.0" / "bin" / "gh"
    closed = folder(tmp_path / "owner-home", 0o700) / "bin"  # a PATH folder in the owner's home: the agent cannot look in
    closed.mkdir()
    (tmp_path / "owner-home").chmod(0o000)
    monkeypatch.setenv("PATH", f"{closed}:{tmp_path / '.local' / 'bin'}:/nowhere")
    real_is_file = Path.is_file

    def is_file_of_python_3_9(path):  # /usr/bin/python3 runs the script: its pathlib re-raises EACCES, 3.14's does not
        if str(path).startswith(str(closed)):
            raise PermissionError(13, "Permission denied", str(path))
        return real_is_file(path)

    monkeypatch.setattr(Path, "is_file", is_file_of_python_3_9)
    try:
        with pytest.raises(agent.Refused, match="run `user` again"):
            agent.real_gh(FakeSh({(mise, "which", "gh"): (1, "")}))  # No PermissionError
    finally:
        (tmp_path / "owner-home").chmod(0o700)
    gh.parent.mkdir(parents=True)
    gh.write_text("")
    sh = FakeSh({(mise, "which", "gh"): (0, f"{gh}\n")})
    assert agent.real_gh(sh) == str(gh)
    assert sh.out_cwds[(mise, "which", "gh")] == tmp_path, "mise runs in the agent's home"


def test_a_key_reaches_the_keychain_on_standard_input_only():
    sh = FakeSh({})
    agent.keychain_set(sh, "linear-key", "lin_api_abc123")
    assert sh.calls == [["security", "-i"]], "never on a command line that ps shows"
    assert sh.stdins == [f"add-generic-password -U -a acme-agent -s acme-agent-agent.linear-key -w lin_api_abc123 {agent.keychain_file()}\n"]
    for bad in ("two words", 'quote"d', "line\nbreak"):
        with pytest.raises(agent.Refused):
            agent.keychain_set(sh, "linear-key", bad)
    assert agent.keychain_get(FakeSh({("security",): (44, "")}), "linear-key") is None


def test_a_typed_key_of_the_wrong_shape_is_not_stored():
    class Typed(agent.Ask):
        def __init__(self, value):
            self.value = value

        def plain(self, prompt):
            return self.value

        secret = pem = plain

    have = {("security", "find-generic-password", "-a", "acme-agent", "-s", f"acme-agent-agent.{name}", "-w"): (0, "x\n")
            for name in ("app-client-id", "app-key", "claude-token", "lithosai-key", "measure-key", "ollama-key")}
    missing = {**have, ("security", "find-generic-password", "-a", "acme-agent", "-s", "acme-agent-agent.linear-key"): (44, "")}
    sh = FakeSh(missing)
    with pytest.raises(agent.Refused, match="linear-key"):
        agent.ask_keys(sh, Typed("sk-ant-not-a-linear-key-at-all"), replace=False)
    assert ["security", "-i"] not in sh.calls
    sh = FakeSh(missing)
    agent.ask_keys(sh, Typed("lin_api_" + "a" * 40), replace=False)
    assert [s for s in sh.stdins if s][-1].endswith(f"acme-agent-agent.linear-key -w lin_api_{'a' * 40} {agent.keychain_file()}\n")
    no_pem = keychain({"app-client-id": "x", "claude-token": "x", "linear-key": "lin_api_x", "lithosai-key": "x",
                       "measure-key": "x", "ollama-key": "x"})
    with pytest.raises(agent.Refused, match="PEM"):
        agent.ask_keys(FakeSh(no_pem), Typed("x" * 400), replace=False)


def test_the_measuring_key_is_optional_and_stored_only_in_its_shape():
    """Enter skips the measuring key (stored nothing, asked again next time); a typed one must look like an
    OpenRouter key."""

    class Typed(agent.Ask):
        def __init__(self, value):
            self.value = value

        def plain(self, prompt):
            assert "Enter skips" in prompt and "never the production key" in prompt, "only the measuring key is asked"
            return self.value

        secret = pem = plain

    others = keychain({"app-client-id": "x", "app-key": "x", "claude-token": "x", "linear-key": "lin_api_x",
                       "lithosai-key": "x", "ollama-key": "x"})
    missing = {**others, ("security", "find-generic-password", "-a", "acme-agent", "-s", "acme-agent-agent.measure-key"): (44, "")}
    sh = FakeSh(missing)
    agent.ask_keys(sh, Typed(""), replace=False)
    assert ["security", "-i"] not in sh.calls, "an empty answer stores nothing"
    sh = FakeSh(missing)
    with pytest.raises(agent.Refused, match="measure-key"):
        agent.ask_keys(sh, Typed("lin_api_" + "a" * 40), replace=False)
    assert ["security", "-i"] not in sh.calls
    sh = FakeSh(missing)
    agent.ask_keys(sh, Typed("sk-or-v1-" + "0f" * 32), replace=False)
    assert [x for x in sh.stdins if x][-1] == f"add-generic-password -U -a acme-agent -s acme-agent-agent.measure-key -w sk-or-v1-{'0f' * 32} {agent.keychain_file()}\n"


def test_a_pasted_pem_is_stored_as_the_base64_of_the_file(app_key):
    """The value app_token signs with is the base64 of the PEM file's own bytes."""

    class Pasted(agent.Ask):
        def pem(self, prompt):
            return app_key.decode()

    sh = FakeSh(keychain({"app-client-id": "x", "claude-token": "x", "linear-key": "lin_api_x", "lithosai-key": "x",
                          "measure-key": "x", "ollama-key": "x"}))
    agent.ask_keys(sh, Pasted(), replace=False)
    stored = [stdin for stdin in sh.stdins if stdin and "acme-agent-agent.app-key" in stdin]
    assert stored == [f"add-generic-password -U -a acme-agent -s acme-agent-agent.app-key -w {base64.b64encode(app_key).decode()} {agent.keychain_file()}\n"]


def paste_into_a_terminal(data: bytes) -> tuple[str, bytes]:
    """What read_pem gives for a paste into a real pseudo-terminal, and what the screen showed."""
    main, side = pty.openpty()
    got: list[str] = []
    reader = threading.Thread(target=lambda: got.append(agent.read_pem(side, "key: ")))
    reader.start()
    shown = b""
    try:
        while not shown.endswith(b"key: "):  # the person pastes after the prompt
            shown += os.read(main, 1024)
        os.write(main, data)
        while reader.is_alive() or select.select([main], [], [], 0)[0]:
            if select.select([main], [], [], 0.1)[0]:
                shown += os.read(main, 4096)
    finally:
        reader.join()
        os.close(main)
        os.close(side)
    return got[0], shown


def test_a_pasted_key_comes_back_as_its_pem_file_in_every_form_with_no_echo(app_key):
    """No form of the paste meets the terminal's 1024-byte line limit, and the key never shows."""
    pem = app_key.decode()
    head, *body, tail = pem.splitlines()
    one_line = base64.b64encode(app_key)
    main, side = pty.openpty()
    limit = os.fpathconf(side, "PC_MAX_CANON")
    os.close(main)
    os.close(side)
    assert len(one_line) > limit and len("".join(pem.splitlines())) > limit, "each one-line form passes the line limit"
    forms = {
        "its own lines": app_key,
        "its lines as a terminal sends them (CR)": app_key.replace(b"\n", b"\r"),
        "one line, spaces between the parts": " ".join([head, *body, tail]).encode(),
        "one line, nothing between the parts": "".join([head, *body, tail]).encode(),
        "the old one-line base64, then Enter": one_line + b"\r",
    }
    for form, data in forms.items():
        got, shown = paste_into_a_terminal(data)
        assert got == pem, form
        assert b"PRIVATE" not in shown and one_line[:40] not in shown and body[0][:40].encode() not in shown, form


@pytest.fixture
def app_key(tmp_path) -> bytes:
    key = tmp_path / "app.pem"
    subprocess.run(["openssl", "genrsa", "-out", str(key), "2048"], check=True, capture_output=True)
    return key.read_bytes()


def keychain(values: dict[str, str]) -> dict[tuple[str, ...], tuple[int, str]]:
    return {("security", "find-generic-password", "-a", "acme-agent", "-s", f"acme-agent-agent.{k}", "-w"): (0, v + "\n")
            for k, v in values.items()}


def test_the_token_comes_from_a_jwt_that_openssl_signs_and_waits_in_the_keychain(tmp_path, app_key):
    now = 1_800_000_000.0
    sh = FakeSh(keychain({"gh-token": f"{int(now) + 200}:ghs_old", "app-client-id": "Iv23liABCDEFGH",
                          "app-key": base64.b64encode(app_key).decode()}))
    asked: list[tuple[str, str, str]] = []

    def api(method, path, bearer):
        asked.append((method, path, bearer))
        return {"id": 42} if method == "GET" else {"token": "ghs_new", "expires_at": "2027-01-15T08:00:00Z"}

    assert agent.app_token(sh, api, lambda: now) == "ghs_new", "a token with under five minutes left is made again"
    assert [(m, p) for m, p, _ in asked] == [("GET", "/repos/maurolopes/acme-agent/installation"),
                                             ("POST", "/app/installations/42/access_tokens")]
    ends = calendar.timegm(time.strptime("2027-01-15T08:00:00Z", "%Y-%m-%dT%H:%M:%SZ"))
    assert sh.stdins[-1].endswith(f"acme-agent-agent.gh-token -w {ends}:ghs_new {agent.keychain_file()}\n")
    head, body, signature = asked[0][2].split(".")
    claims = json.loads(base64.urlsafe_b64decode(body + "=="))
    assert claims == {"iat": int(now) - 60, "exp": int(now) + 540, "iss": "Iv23liABCDEFGH"}
    (tmp_path / "data").write_bytes(f"{head}.{body}".encode())
    (tmp_path / "sig").write_bytes(base64.urlsafe_b64decode(signature + "=="))
    public = subprocess.run(["openssl", "rsa", "-pubout"], input=app_key, capture_output=True, check=True).stdout
    (tmp_path / "pub.pem").write_bytes(public)
    verified = subprocess.run(["openssl", "dgst", "-sha256", "-verify", str(tmp_path / "pub.pem"), "-signature",
                               str(tmp_path / "sig"), str(tmp_path / "data")], capture_output=True, text=True, check=False)
    assert verified.returncode == 0, verified.stdout + verified.stderr

    fresh = FakeSh(keychain({"gh-token": f"{int(now) + 3000}:ghs_cached"}))
    assert agent.app_token(fresh, lambda *a: pytest.fail("no request for a fresh token"), lambda: now) == "ghs_cached"


def test_git_gets_the_token_for_github_only(monkeypatch):
    monkeypatch.setattr(agent, "app_token", lambda sh: "ghs_tok")
    asked = "protocol=https\nhost=github.com\n\n"
    assert agent.git_credential(FakeSh({}), "get", asked) == "username=x-access-token\npassword=ghs_tok\n"
    assert agent.git_credential(FakeSh({}), "get", "protocol=https\nhost=example.com\n") == ""
    assert agent.git_credential(FakeSh({}), "store", asked) == ""


def test_user_sets_up_the_clone_with_the_keys_already_there(tmp_path):
    home = folder(tmp_path / "acme-agent", 0o700)
    for tool in ("mise", "claude", "pi"):
        folder(home / ".local" / "bin", 0o700)
        (home / ".local" / "bin" / tool).write_text("")
    folder(home / "Library" / "Keychains", 0o700)
    (home / "Library" / "Keychains" / "agent.keychain-db").write_text("")
    sh = FakeSh(keychain({name: "x" for name, *_ in agent.KEYS}))
    with pytest.raises(agent.Refused, match="sudo -iu acme-agent"):
        agent.user(sh, NoAsk(), home, "owner", replace=False)
    agent.user(sh, NoAsk(), home, "acme-agent", replace=False)
    agent.user(sh, NoAsk(), home, "acme-agent", replace=False)  # again: the profile lines stay single
    assert not any("curl" in " ".join(c) for c in sh.calls), "mise and claude were there"
    clone = home / "acme-agent"
    assert ["git", "clone", "https://github.com/maurolopes/acme-agent.git", str(clone)] in sh.calls
    mise = str(home / ".local" / "bin" / "mise")
    steps = [c for c in sh.calls if c[0] == mise]
    assert steps[:9] == [[mise, "trust"], [mise, "install"], [mise, "use", "-g", "github-cli@2.96.0"],
                         [mise, "unuse", "-g", "herdr"], [mise, "prune", "-y", "herdr"], [mise, "use", "-g", f"node@{agent.NODE_VERSION}"], [mise, "reshim"], [mise, "run", "hooks"],
                         [mise, "run", "start:deps"]], "The agent's own gh, no herdr in mise, its own shims"
    assert (home / ".zshenv").read_text() == 'export PATH="$HOME/.local/bin:$HOME/.local/share/mise/shims:/usr/bin:/bin:/usr/sbin:/sbin"\n'
    assert (home / ".zshrc").read_text().splitlines().count('alias claude="/usr/bin/python3 -I $HOME/agent_user.py session"') == 1
    assert (home / ".zshrc").read_text().splitlines().count('alias agent="/usr/bin/python3 -I $HOME/agent_user.py session"') == 1
    assert (home / ".zshrc").read_text().splitlines().count('alias pi="/usr/bin/python3 -I $HOME/agent_user.py pi"') == 1
    clear = ["git", "config", "--global", "--replace-all", "credential.https://github.com.helper", ""]
    add = ["git", "config", "--global", "--add", "credential.https://github.com.helper",
           f"!/usr/bin/python3 -I {home / 'agent_user.py'} git-credential"]
    assert sh.calls.index(clear) + 1 == sh.calls.index(add), "the empty helper clears osxkeychain first"
    shim = home / ".local" / "bin" / "gh"
    assert shim.stat().st_mode & 0o777 == 0o700 and '"$HOME/agent_user.py" gh "$@"' in shim.read_text()
    assert (home / ".zprofile").read_text().splitlines().count("umask 077") == 1
    assert json.loads((home / ".claude" / "settings.json").read_text()) == {"skipDangerousModePermissionPrompt": True}


def test_user_makes_claude_code_skip_its_one_time_bypass_mode_question_and_keeps_every_other_setting(tmp_path):
    """A herdr pane on a profile with `bypassPermissions` waits for ever at the question, whose default answer is
    "No, exit" (measured 2026-11-20, Claude Code 2.1.328). The setting is the only change, and a second run changes nothing.
    The setting is Claude Code's entry in HARNESSES."""
    claude_code = agent.HARNESSES["claude-code"]
    assert (claude_code.settings_file, claude_code.settings) == (".claude/settings.json", (("skipDangerousModePermissionPrompt", True),))
    settings = tmp_path / ".claude" / "settings.json"
    agent.write_harness_settings(tmp_path, claude_code)  # no folder, no file yet
    assert json.loads(settings.read_text()) == {"skipDangerousModePermissionPrompt": True}
    for other in (False, 1):  # 1 is no JSON true
        settings.write_text(json.dumps({"model": "haiku", "hooks": {"x": [1]}, "skipDangerousModePermissionPrompt": other}))
        agent.write_harness_settings(tmp_path, claude_code)
        assert json.loads(settings.read_text()) == {"model": "haiku", "hooks": {"x": [1]}, "skipDangerousModePermissionPrompt": True}
    before = settings.stat().st_mtime_ns
    agent.write_harness_settings(tmp_path, claude_code)
    assert settings.stat().st_mtime_ns == before, "already set: not written again"
    assert not (tmp_path / ".claude" / ".settings.json.new").exists()
    for bad in ("{not json", "[1, 2]"):  # never overwritten: the owner fixes it
        settings.write_text(bad)
        with pytest.raises(agent.Refused, match=r"settings\.json"):
            agent.write_harness_settings(tmp_path, claude_code)
        assert settings.read_text() == bad
    agent.write_harness_settings(tmp_path / "none", claude_code._replace(settings_file="", settings=()))
    assert not (tmp_path / "none").exists(), "a harness with no settings gets no file"


def test_the_keychain_is_the_agent_s_own_by_its_full_path_and_gets_its_settings_while_open(tmp_path):
    """The settings wait on a locked keychain. A keychain named login refused its own password."""
    keychain = str(tmp_path / "Library" / "Keychains" / "agent.keychain-db")
    settings = [["security", "list-keychains", "-d", "user", "-s", keychain],
                ["security", "default-keychain", "-d", "user", "-s", keychain],
                ["security", "set-keychain-settings", keychain]]
    new = FakeSh({})
    assert agent.ensure_keychain(new, tmp_path) == []
    assert new.calls == [["security", "create-keychain", keychain], *settings]
    folder(tmp_path / "Library" / "Keychains", 0o700)
    Path(keychain).write_text("")
    (tmp_path / "Library" / "Keychains" / "login.keychain-db").write_text("")  # an older run's
    there = FakeSh({})
    notes = agent.ensure_keychain(there, tmp_path)
    assert there.calls == [["security", "unlock-keychain", keychain], *settings], "a second run"
    assert notes and "security delete-keychain" in notes[0] and "login.keychain-db" in notes[0]
    locked = FakeSh({("security", "unlock-keychain"): (51, "")})
    with pytest.raises(agent.Refused):
        agent.ensure_keychain(locked, tmp_path)
    assert locked.calls == [["security", "unlock-keychain", keychain]], "no settings on a locked keychain"
    every = [*new.calls, *there.calls, *locked.calls]
    assert not any(arg.startswith("login.keychain") for call in every for arg in call), "never the bare login name"


def test_every_keychain_read_and_write_names_the_agent_s_keychain():
    sh = FakeSh(keychain({"linear-key": "lin_api_x"}))
    agent.keychain_get(sh, "linear-key")
    agent.keychain_set(sh, "linear-key", "lin_api_y")
    locked = FakeSh({})
    agent.unlock(locked)
    assert locked.calls[-1] == ["security", "unlock-keychain", agent.keychain_file()]
    assert sh.calls[0][-1] == agent.keychain_file() and sh.stdins[-1].endswith(f" {agent.keychain_file()}\n")
    assert agent.keychain_file().endswith("/Library/Keychains/agent.keychain-db")


def test_every_name_is_the_repository_s_and_a_name_outside_the_rule_is_refused_not_edited():
    """One name for the repository, the user, its clone and its Keychain service."""
    assert (agent.USER, agent.CLONE, agent.SERVICE) == ("acme-agent", Path("acme-agent"), "acme-agent-agent")
    assert agent.project_name("someone/a1-b2") == "a1-b2"
    for bad in ("Acme-Agent", "acme_agent", "-acme", "acme-", "1acme", "k" * 33):
        with pytest.raises(ValueError, match="lowercase letters"):
            agent.project_name(f"someone/{bad}")
    assert agent.project_name("someone/" + "k" * 32) == "k" * 32


def _account(monkeypatch, name: str, gid: int) -> None:
    monkeypatch.setattr(agent.pwd, "getpwuid", lambda uid: agent.pwd.struct_passwd(
        (name, "*", 503, gid, "", f"/Users/{name}", "/bin/zsh")))


def _checkout(root: Path, url: str, worktree: bool = False) -> Path:
    """A checkout whose `origin` is `url`; with `worktree`, a linked worktree of it (`.git` is a file)."""
    git = root / "main" / ".git"
    (git / "worktrees" / "wt").mkdir(parents=True)
    (git / "config").write_text(f'[core]\n\tbare = false\n[remote "upstream"]\n\turl = https://github.com/x/other.git\n'
                                f'[remote "origin"]\n\turl = {url}\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n')
    if not worktree:
        return root / "main"
    (git / "worktrees" / "wt" / "commondir").write_text("../..\n")
    (root / "wt").mkdir()
    (root / "wt" / ".git").write_text(f"gitdir: {git / 'worktrees' / 'wt'}\n")
    return root / "wt"


def test_the_agents_account_takes_its_login_name_from_the_uid_never_from_dollar_user(tmp_path, monkeypatch):
    """The owner's call of 2026-11-24: after `su` from the owner's account, $USER is still the owner's name."""
    _account(monkeypatch, "proj-a", agent.GID)
    assert agent.agent_name({"USER": "owner"}, 503, _checkout(tmp_path, "https://github.com/x/proj-b.git")) == "proj-a"


def test_root_and_the_owner_take_the_name_of_the_checkout_s_origin(tmp_path, monkeypatch):
    _account(monkeypatch, "owner", 20)
    for url in ("https://github.com/x/proj-b.git", "git@github.com:x/proj-b.git", "https://github.com/x/proj-b/"):
        for worktree in (False, True):
            (tmp_path / url.replace("/", "_")).mkdir(exist_ok=True)
            checkout = _checkout(tmp_path / url.replace("/", "_") / str(worktree), url, worktree)
            assert agent.agent_name({}, 0, checkout) == "proj-b", (url, worktree)


def test_a_relative_gitdir_a_worktree_with_no_commondir_and_a_uid_with_no_account(tmp_path, monkeypatch):
    """A `.git` file may name its folder relative to the checkout, and that folder may hold the config itself."""
    (tmp_path / "gitdir").mkdir()
    (tmp_path / "gitdir" / "config").write_text('[remote "origin"]\n\turl = https://github.com/x/proj-d.git\n')
    (tmp_path / "wt").mkdir()
    (tmp_path / "wt" / ".git").write_text("gitdir: ../gitdir\n")

    def no_account(uid):
        raise KeyError(uid)

    monkeypatch.setattr(agent.pwd, "getpwuid", no_account)
    assert agent.agent_name({}, 4242, tmp_path / "wt") == "proj-d"


def test_agent_user_in_the_environment_comes_first_and_meets_the_name_rule(tmp_path, monkeypatch):
    _account(monkeypatch, "proj-a", agent.GID)
    assert agent.agent_name({"AGENT_USER": "proj-c"}, 503, tmp_path) == "proj-c"
    with pytest.raises(ValueError, match="lowercase letters"):
        agent.agent_name({"AGENT_USER": "Proj_C"}, 503, tmp_path)


def test_no_origin_outside_the_agents_group_is_refused_with_the_way_out(tmp_path, monkeypatch):
    _account(monkeypatch, "owner", 20)
    with pytest.raises(ValueError, match="set AGENT_USER"):
        agent.agent_name({}, 501, tmp_path)
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text('[remote "upstream"]\n\turl = https://github.com/x/other.git\n')
    with pytest.raises(ValueError, match="set AGENT_USER"):
        agent.agent_name({}, 501, tmp_path)


def test_the_file_holds_no_name_of_its_own_project_and_the_names_follow_the_one_name():
    """The keychain items, the service label and the clone all come from USER, which the file does not hold; the
    loop's other repositories come from the clone's AGENTS.md."""
    text = (REPO / ".workflow" / "agent_user.py").read_text()
    assert f'"{agent.USER}' not in text and f"/{agent.USER}" not in text
    assert (agent.SERVICE, agent.ORCHESTRATOR_LABEL, agent.REPO) == (
        f"{agent.USER}-agent", f"com.{agent.USER}.orchestrator", f"{agent.GITHUB_OWNER}/{agent.USER}")


def test_a_session_gets_its_keys_and_no_api_key(tmp_path):
    sh = FakeSh(keychain({"claude-token": "sk-ant-oat-x", "linear-key": "lin_api_y"}))
    env = agent.session_env(sh, tmp_path, {"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "sk-ant-api", "GH_TOKEN": "owner"})
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat-x" and env["LINEAR_API_KEY"] == "lin_api_y"
    assert "ANTHROPIC_API_KEY" not in env and "GH_TOKEN" not in env, "the subscription's token, and gh through the shim"
    assert env["PATH"].split(":")[0] == str(tmp_path / ".local" / "bin")


def test_a_session_gets_the_measuring_key_under_its_own_name_only_when_stored(tmp_path):
    """The measuring key reaches a session (herdr's `claude`, Paseo's daemon) as MEASURE_OPENROUTER_API_KEY, never
    as the OPENROUTER_API_KEY the product reads; a caller's production key never reaches it; no stored key, no variable."""
    keys = {"claude-token": "sk-ant-oat-x", "linear-key": "lin_api_y"}
    caller = {"PATH": "/usr/bin", "OPENROUTER_API_KEY": "sk-or-production", "MEASURE_OPENROUTER_API_KEY": "sk-or-inherited"}
    env = agent.session_env(FakeSh(keychain({**keys, "measure-key": "sk-or-v1-measure"})), tmp_path, caller)
    assert env["MEASURE_OPENROUTER_API_KEY"] == "sk-or-v1-measure", "the Keychain's key, never one the caller had"
    assert "OPENROUTER_API_KEY" not in env, "no model call by accident, and never the production key"
    none = FakeSh({**keychain(keys), ("security", "find-generic-password", "-a", "acme-agent", "-s", "acme-agent-agent.measure-key"): (44, "")})
    env = agent.session_env(none, tmp_path, caller)
    assert env["LINEAR_API_KEY"] == "lin_api_y", "a session starts without the measuring key"
    assert not {"OPENROUTER_API_KEY", "MEASURE_OPENROUTER_API_KEY"} & env.keys()
    env = agent.run_env(FakeSh(keychain({**keys, "measure-key": "sk-or-v1-measure"})), tmp_path, caller)
    assert not {"OPENROUTER_API_KEY", "MEASURE_OPENROUTER_API_KEY"} & env.keys(), "the orchestrator's poll holds the least key"


def test_a_session_gets_linear_api_key_only_and_never_one_the_caller_had(tmp_path):
    """The team OWL is in this workspace, so a session's Linear key is LINEAR_API_KEY from the Keychain. No
    team has an item of its own, and the caller's own LINEAR_API_KEY is dropped."""
    keys = {"claude-token": "sk-ant-oat-x", "linear-key": "lin_api_y"}
    caller = {"PATH": "/usr/bin", "LINEAR_API_KEY": "lin_api_inherited"}
    env = agent.session_env(FakeSh(keychain(keys)), tmp_path, caller)
    assert env["LINEAR_API_KEY"] == "lin_api_y"
    assert not any("owl" in name for name, *_ in agent.KEYS) and not any("owl" in name for name in agent.OPTIONAL_KEYS)


def test_each_team_s_key_variable_of_the_teams_table_is_one_that_a_session_gets():
    """AGENTS.md "The teams" names each team's key variable; each one is a session's key."""
    table = (REPO / "AGENTS.md").read_text().split("\n## The teams\n", 1)[1].split("\n## ", 1)[0]
    variables = set(re.findall(r"^\| [A-Z]+ \| \S+ \|.*\| `([A-Z_]+)` \|$", table, flags=re.M))
    assert variables == {"LINEAR_API_KEY"}
    sessions = {var for _, var in (*agent.SESSION_KEYS, *agent.SESSION_OPTIONAL_KEYS)}
    assert variables <= sessions and variables <= set(agent.CALLER_KEYS)


def test_every_keys_env_environment_has_the_local_review_command(tmp_path):
    """A session (herdr's `claude`, Paseo's daemon) and `run` all get LOCAL_REVIEW_COMMAND, with the agent's
    keychain read at run time and no token in the text."""
    sh = FakeSh(keychain({"claude-token": "sk-ant-oat-x", "linear-key": "lin_api_y"}))
    for env in (agent.session_env(sh, tmp_path, {"PATH": "/usr/bin"}), agent.run_env(sh, tmp_path, {"PATH": "/usr/bin"})):
        command = env["LOCAL_REVIEW_COMMAND"]
        assert command.startswith("CLAUDE_CODE_OAUTH_TOKEN=$(/usr/bin/security find-generic-password -a acme-agent -s acme-agent-agent.claude-token")
        assert str(tmp_path / "Library" / "Keychains" / "agent.keychain-db") in command
        assert '"$LOCAL_REVIEW_SCHEMA"' in command and "sk-ant-oat-x" not in command
    env = agent.session_env(sh, tmp_path, {"PATH": "/usr/bin", "LOCAL_REVIEW_COMMAND": "caller's"})
    assert env["LOCAL_REVIEW_COMMAND"] != "caller's", "one definition: the caller's value never wins"


def test_a_pi_session_uses_the_configured_review_model_without_rewriting_models(review_file, tmp_path):
    settings = json.loads(review_file.read_text())
    settings["pi-ollama"] = {"model": "glm-5.3-flash", "effort": "high"}
    review_file.write_text(json.dumps(settings))
    home = tmp_path / "home"
    models = home / ".pi" / "agent" / "models.json"
    models.parent.mkdir(parents=True)
    models.write_text(json.dumps({"providers": {"other": {"baseUrl": "https://x", "models": []}}}))
    sh = FakeSh(keychain({"linear-key": "lin_api_y", "ollama-key": "abc123def456.ollama-key-value"}))
    env = agent.session_env(sh, home, {"PATH": "/usr/bin"}, "pi-ollama")
    assert "--model pi-ollama/glm-5.3-flash --thinking high" in env["LOCAL_REVIEW_COMMAND"]
    assert json.loads(models.read_text()) == {"providers": {"other": {"baseUrl": "https://x", "models": []}}}


def test_claude_clears_the_caller_s_provider_and_model_overrides(tmp_path):
    sh = FakeSh(keychain({"claude-token": "sk-ant-oat-x", "linear-key": "lin_api_y", "ollama-key": "abc123def456.ollama-key-value"}))
    caller = {"PATH": "/usr/bin", "ANTHROPIC_BASE_URL": "https://elsewhere.example", "ANTHROPIC_AUTH_TOKEN": "theirs",
              "OLLAMA_API_KEY": "theirs", "CLAUDE_CODE_OAUTH_TOKEN": "theirs", "ANTHROPIC_DEFAULT_HAIKU_MODEL": "claude-haiku"}
    claude = agent.session_env(sh, tmp_path, caller, "claude")
    assert claude["LINEAR_API_KEY"] == "lin_api_y" and claude["PATH"].split(":")[0] == str(tmp_path / ".local" / "bin")
    assert "OLLAMA_API_KEY" not in claude
    assert claude == agent.session_env(sh, tmp_path, caller) and claude["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat-x"
    assert not {"ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "ANTHROPIC_DEFAULT_HAIKU_MODEL"} & claude.keys()
    without = FakeSh(keychain({"claude-token": "sk-ant-oat-x", "linear-key": "lin_api_y"}))
    with pytest.raises(agent.Refused, match="ollama-key is not in the Keychain"):
        agent.session_env(without, tmp_path, caller, "pi-ollama")


def test_a_session_reviews_on_the_review_profile_it_is_given(tmp_path):
    """`session --profile CELL --review-profile IN_FORCE` runs the cell, and its local review is the profile in
    force's."""
    assert agent.take_profile(["--profile", "pi-ollama", "--review-profile", "claude", "--x"]) == ("pi-ollama", "claude", ["--x"])
    assert agent.take_profile(["--profile", "pi-ollama", "--x"]) == ("pi-ollama", "pi-ollama", ["--x"])
    assert agent.take_profile(["--x"]) == (agent.DEFAULT_PROFILE, agent.DEFAULT_PROFILE, ["--x"])
    with pytest.raises(agent.Refused, match="--review-profile takes one of"):
        agent.take_profile(["--profile", "claude", "--review-profile", "nope"])
    sh = FakeSh(keychain({"claude-token": "sk-ant-oat-x", "linear-key": "lin_api_y", "ollama-key": "abc123def456.ollama-key-value"}))
    models = agent.review_settings()
    assert models["claude"] != models["pi-ollama"]
    env = agent.session_env(sh, tmp_path, {"PATH": "/usr/bin"}, "claude", "pi-ollama")
    assert f"--model pi-ollama/{models['pi-ollama'][0]}" in env["LOCAL_REVIEW_COMMAND"]
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat-x"  # the session itself stays on the cell's profile


def test_the_local_review_command_follows_the_profile(tmp_path):
    """The review reads the profile's key at run time, after the provider's variables; --max-budget-usd counts
    Anthropic's prices, so only the subscription's command has it."""
    keychain_file = tmp_path / "agent.keychain-db"
    claude = agent.review_command(agent.PROFILES["claude"], keychain_file, REVIEW["claude"])
    assert claude == ("CLAUDE_CODE_OAUTH_TOKEN=$(/usr/bin/security find-generic-password -a acme-agent -s acme-agent-agent.claude-token "
                      f"-w {keychain_file}) claude -p --model claude-sonnet-5-5 --effort high --tools \"Read,Grep,Glob\" --strict-mcp-config "
                      "--setting-sources user --no-session-persistence --output-format json --json-schema \"$LOCAL_REVIEW_SCHEMA\" "
                      "--max-budget-usd 4")


def test_each_profile_names_its_review_command_and_the_command_comes_from_it(tmp_path):
    """The review's command is the profile's `review_cli`, with the model, the effort and the budget filled in."""
    for profile in agent.PROFILES.values():
        assert "{model}" in profile.review_cli
        # Claude Code's print mode takes the schema as a flag; pi's has none, and the prompt gives it
        assert ("$LOCAL_REVIEW_SCHEMA" in profile.review_cli) == (profile.harness == "claude-code")
    custom = agent.PROFILES["claude"]._replace(review_cli="other -p --model {model} --effort {effort}{budget}")
    command = agent.review_command(custom, tmp_path / "k", REVIEW["claude"])
    assert command.endswith(") other -p --model claude-sonnet-5-5 --effort high --max-budget-usd 4")
    # the model and the effort level are the settings', not the profile's own: a review on another model is one line
    other = agent.review_command(custom, tmp_path / "k", ("claude-opus-5-5", "medium"))
    assert other.endswith(") other -p --model claude-opus-5-5 --effort medium --max-budget-usd 4")


def test_the_profiles_are_the_orchestrator_s_and_each_key_check_names_its_profile_s_item():
    """The orchestrator's [profiles.NAME] tables hold the run cells and the key's check; this file the provider.
    A profile on pi's own sign-in has no item to name: its check runs `pi-signin` on that provider instead, and
    the table says so with `credential = "sign-in"`, and every table names the local review's model and effort level."""
    tables = tomllib.loads((REPO / "orchestrator" / "orchestrator.example.toml").read_text())["profiles"]
    assert set(tables) == set(agent.PROFILES) and agent.DEFAULT_PROFILE in tables
    for name, table in tables.items():
        profile = agent.PROFILES[name]
        if profile.provider:  # pi signs itself in: nothing in the keychain, only the file that pi keeps
            assert (profile.key, profile.variable, table["credential"]) == ("", "", "sign-in")
            assert table["key_check"].endswith(f"pi-signin --provider {profile.provider}"), "the same provider here and there"
            continue
        assert table.get("credential", "keychain") == "keychain", "any other profile keeps its key in the keychain"
        assert re.search(rf"-s \S+\.{re.escape(profile.key)} ", table["key_check"]), "the item ends with the profile's key; its prefix is the installed config's own"
        assert (table["review_model"], table["review_effort"]) == REVIEW[name], "the config's, which the install writes"
    assert all(profile.key in {k for k, *_ in agent.KEYS} for profile in agent.PROFILES.values() if profile.key)


def test_the_local_review_s_settings_are_the_install_s_file_and_a_bad_file_starts_nothing(tmp_path, review_file):
    """Each profile's review model and effort level come from the orchestrator's config, which `install` and
    `restart` write as JSON beside the installed copy (the script runs on Python 3.9, which reads no TOML). A session starts
    only on a file that names every profile with its model and effort level, so a review runs on the model that the config
    names, never on one that nobody chose."""
    assert agent.review_settings() == REVIEW
    sh = FakeSh(keychain({"claude-token": "sk-ant-oat-x"}))
    for broken, said in (
        ({"claude": {"model": "m", "effort": "high"}}, "names no local review for pi-chatgpt"),
        ({**{n: {"model": "m", "effort": "high"} for n in REVIEW}, "gone": {"model": "m", "effort": "high"}},
         "names the profile 'gone', which this script does not know"),
        ({**{n: {"model": "m", "effort": "high"} for n in REVIEW}, "pi-ollama": {"model": "m"}}, "needs a model and an effort"),
        ({**{n: {"model": "m", "effort": "high"} for n in REVIEW}, "claude": {"model": "a b", "effort": "high"}},
         "needs a model and an effort"),
    ):
        review_file.write_text(json.dumps(broken))
        with pytest.raises(agent.Refused, match=said):
            agent.session_env(sh, tmp_path, {"PATH": "/usr/bin"})
    review_file.write_text("not json")
    with pytest.raises(agent.Refused, match="is not JSON"):
        agent.review_settings()
    review_file.unlink()
    with pytest.raises(agent.Refused, match=r"cannot read .*: run it as the owner"):
        agent.run_env(sh, tmp_path, {"PATH": "/usr/bin"})
    assert sh.calls == [], "the session is refused before a key is read"


def test_the_settings_file_is_the_one_beside_the_copy_and_else_the_service_s(tmp_path, monkeypatch):
    """A copy reads the file beside itself when it has one, which is the service's own and the staged copy that an
    install's dry round runs (so a new profile of the config is no refusal there); a copy in the agent's home, which `admin`
    writes alone, reads the one in the folder that the service runs from, which its plist names."""
    install = tmp_path / "usr/local/lib/orchestrator"
    install.mkdir(parents=True)
    staged = tmp_path / "staged"
    staged.mkdir()
    plist = tmp_path / "Library/LaunchAgents/com.x.orchestrator.plist"
    plist.parent.mkdir(parents=True)
    plist.write_bytes(plistlib.dumps({"WorkingDirectory": str(install)}))
    monkeypatch.setattr(agent, "review_file", REAL_REVIEW_FILE)
    monkeypatch.setattr(agent, "ORCHESTRATOR_PLIST", plist)
    monkeypatch.setattr(agent, "HERE", staged)
    assert agent.review_file() == install / agent.REVIEW_FILE, "no file beside the copy: the service's folder, from the plist"
    (staged / agent.REVIEW_FILE).write_text("{}")
    assert agent.review_file() == staged / agent.REVIEW_FILE, "the one beside the copy first: an install's staged dry round"
    (staged / agent.REVIEW_FILE).unlink()
    for no_folder in (plistlib.dumps({}), plistlib.dumps({"WorkingDirectory": "not/absolute"})):
        plist.write_bytes(no_folder)
        with pytest.raises(agent.Refused, match="names no WorkingDirectory: run `owlcode install`"):
            agent.review_file()
    plist.write_bytes(b"not a plist")
    with pytest.raises(agent.Refused, match=r"cannot read .*: run `owlcode install`"):
        agent.review_file()  # a plist that does not parse, as one that is not there: the owner runs the install
    monkeypatch.setattr(agent, "ORCHESTRATOR_PLIST", tmp_path / "none.plist")
    with pytest.raises(agent.Refused, match=r"cannot read .*: run `owlcode install`"):
        agent.review_file()


def test_the_profile_in_force_is_the_install_s_file_and_a_bad_file_starts_nothing(tmp_path, monkeypatch):
    """`paseo-daemon` takes the profile from `inference-profile.json`, which `install` and `restart` write from the
    config's `inference_profile`; a missing or broken file, or a profile this script does not know, starts nothing."""
    monkeypatch.setattr(agent, "profile_in_force", REAL_PROFILE_IN_FORCE)
    monkeypatch.setattr(agent, "HERE", tmp_path)
    path = tmp_path / agent.PROFILE_FILE
    path.write_text(json.dumps({"inference_profile": "pi-lithosai"}))
    assert agent.profile_in_force() == "pi-lithosai"
    for broken, why in (('{"inference_profile": "nobody"}', "names no profile"), ("[]", "names no profile"),
                        ("not json", "cannot read")):
        path.write_text(broken)
        with pytest.raises(agent.Refused, match=why):
            agent.profile_in_force()
    path.unlink()
    monkeypatch.setattr(agent, "install_folder", lambda: tmp_path / "service")
    with pytest.raises(agent.Refused, match=r"cannot read .*service/inference-profile.json .*owlcode restart"):
        agent.profile_in_force()


def test_run_gets_the_linear_key_and_the_shim_first_and_no_other_key(tmp_path):
    """The orchestrator's poll as the agent, outside a Claude Code session."""
    sh = FakeSh(keychain({"claude-token": "sk-ant-oat-x", "linear-key": "lin_api_y"}))
    env = agent.run_env(sh, tmp_path, {"PATH": "/mise/gh/bin:/usr/bin", "HOME": "/Users/acme-agent",
                                       "ANTHROPIC_API_KEY": "sk-ant-api", "GH_TOKEN": "owner", "GITHUB_TOKEN": "owner",
                                       "CLAUDE_CODE_OAUTH_TOKEN": "inherited", "LINEAR_API_KEY": "inherited"})
    assert env["LINEAR_API_KEY"] == "lin_api_y", "the Keychain's key, never one the caller had"
    assert not {"ANTHROPIC_API_KEY", "GH_TOKEN", "GITHUB_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"} & env.keys(), "least key"
    assert env["PATH"].split(":") == [str(tmp_path / ".local" / "bin"), "/mise/gh/bin", "/usr/bin"], "the gh shim wins"
    assert env["HOME"] == "/Users/acme-agent"
    assert not any("acme-agent-agent.claude-token" in call for call in sh.calls), "the Claude token is never read"
    with pytest.raises(agent.Refused, match="linear-key is not in the Keychain: run `user`"):
        agent.run_env(FakeSh({("security", "find-generic-password"): (44, "")}), tmp_path, {})


def test_run_from_the_installed_copy_puts_its_own_gh_shim_before_the_agent_s(tmp_path, monkeypatch):
    """The service's gh is the root-owned shim beside the installed agent_user.py, never ~/.local/bin/gh (the
    agent's); herdr stays in ~/.local/bin. A copy with no bin/gh beside it keeps the agent's shim first."""
    installed = tmp_path / "installed"
    (installed / "bin").mkdir(parents=True)
    sh = FakeSh(keychain({"linear-key": "lin_api_y"}))
    monkeypatch.setattr(agent, "HERE", installed)
    env = agent.run_env(sh, tmp_path, {"PATH": "/usr/bin"})
    assert env["PATH"].split(":") == [str(tmp_path / ".local" / "bin"), "/usr/bin"], "no gh beside it: no folder added"
    (installed / "bin" / "gh").write_text("#!/bin/sh\n")
    env = agent.run_env(sh, tmp_path, {"PATH": "/usr/bin"})
    assert env["PATH"].split(":") == [str(installed / "bin"), str(tmp_path / ".local" / "bin"), "/usr/bin"]


def test_gh_skips_every_shim_folder_on_path(tmp_path, monkeypatch):
    """The installed shim runs `agent_user.py gh`: a gh found in its own folder would run the shim again, without end."""
    installed, real = tmp_path / "installed", tmp_path / "real"
    for folder in (installed / "bin", tmp_path / ".local" / "bin", real):
        folder.mkdir(parents=True)
        (folder / "gh").write_text("")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(agent, "HERE", installed)
    monkeypatch.setenv("PATH", f"{installed / 'bin'}:{tmp_path / '.local' / 'bin'}:{real}")
    assert agent.real_gh(FakeSh({})) == str(real / "gh")


def test_run_hands_the_command_its_own_arguments_and_the_key_only_in_its_environment(tmp_path, monkeypatch):
    ran: list[tuple[str, list[str], dict[str, str]]] = []
    unlocked: list[bool] = []
    monkeypatch.setattr(agent, "unlock", lambda sh: unlocked.append(True))
    monkeypatch.setattr(agent, "keychain_get", lambda sh, name: "lin_api_y" if name == "linear-key" else None)

    def execvpe(file, argv, env):  # the real one never returns
        ran.append((file, argv, env))
        raise SystemExit(0)

    monkeypatch.setattr(agent.os, "execvpe", execvpe)
    for argv in (["run", "--", "uv", "run", "python", "o.py", "--once"], ["run", "gh", "pr", "list", "--json", "number"]):
        with pytest.raises(SystemExit):
            agent.main(argv)
    assert [(f, a) for f, a, _ in ran] == [("uv", ["uv", "run", "python", "o.py", "--once"]),
                                           ("gh", ["gh", "pr", "list", "--json", "number"])], "the command's flags are its own"
    assert unlocked == [True, True], "a locked keychain asks for its password first"
    assert all(env["LINEAR_API_KEY"] == "lin_api_y" and "lin_api_y" not in argv for _, argv, env in ran)
    for empty in (["run"], ["run", "--"]):
        assert agent.main(empty) == 1, "no command: the usage, and nothing runs"
    assert len(ran) == 2


def test_paseo_daemon_starts_only_paseo_s_daemon_with_a_session_s_keys(tmp_path, monkeypatch, capsys, in_force):
    """Pass the session's keys to daemon start, not to status. Record only the new daemon's profile."""
    ran = []
    unlocked: list[bool] = []
    keys = {"claude-token": "sk-ant-oat-x", "linear-key": "lin_api_y"}
    steps: list[str] = []
    monkeypatch.setattr(agent, "unlock", lambda sh: unlocked.append(True) or steps.append("unlock"))
    monkeypatch.setattr(agent, "load_orchestrator", lambda sh, uid: steps.append(f"load {uid}") or "loaded")
    monkeypatch.setattr(agent, "wait_for_tailnet", lambda sh: steps.append("tailnet") or "100.113.228.95")
    monkeypatch.setattr(agent, "keychain_get", lambda sh, name: keys.get(name))
    monkeypatch.setattr(agent.pwd, "getpwuid", lambda uid: agent.pwd.struct_passwd(
        (agent.USER, "*", 501, 20, "", str(tmp_path), "/bin/zsh")))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api-z")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    status = {"localDaemon": "stopped"}
    cli = str(tmp_path / ".local/bin/paseo")
    marker = tmp_path / agent.PASEO_PROFILE
    marker.parent.mkdir(parents=True)
    marker.write_text("broken record")

    def observe(sh, argv):
        assert argv == [cli, "daemon", "status", "--json"]
        return subprocess.CompletedProcess(argv, 0, json.dumps(status))

    def start(sh, argv, cwd=None, env=None):
        ran.append((argv, cwd, env))
        status.update(localDaemon="running", pid=123, startedAt="start-time")
        return 0

    monkeypatch.setattr(agent.Sh, "tty", start)
    monkeypatch.setattr(agent.Sh, "out", observe)
    assert agent.main(["paseo-daemon"]) == 0
    [(argv, cwd, env)] = ran
    assert argv == [cli, "daemon", "start"] and cwd == tmp_path
    assert json.loads(marker.read_text()) == {
        "profile": "claude", "profiles": ["claude"], "pid": 123, "startedAt": "start-time", "review": agent.review_digest(["claude"])}
    assert unlocked == [True], "a locked keychain asks for its password first"
    assert steps == ["unlock", f"load {os.getuid()}", "tailnet"], "The daemon starts only once Tailscale's address is there"
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat-x" and env["LINEAR_API_KEY"] == "lin_api_y"
    assert "ANTHROPIC_API_KEY" not in env, "Claude Code prefers an API key to the subscription's token"
    assert env["PATH"].split(os.pathsep)[0] == str(tmp_path / ".local" / "bin"), "`gh` is the shim with the app's token"
    with pytest.raises(SystemExit):
        agent.main(["paseo-daemon", "--", "sh", "-c", "env"])  # argparse refuses another command
    keys["ollama-key"] = "abc123def456.ollama-key-value"
    capsys.readouterr()
    assert agent.main(["paseo-daemon", "--profile", "pi-ollama"]) == 1, "Only the profile in force"
    assert "--profile pi-ollama is not the orchestrator's inference_profile, claude" in capsys.readouterr().err
    in_force["name"] = "pi-ollama"
    assert agent.main(["paseo-daemon", "--profile", "pi-ollama"]) == 1
    assert len(ran) == 1 and json.loads(marker.read_text())["profile"] == "claude"
    status["localDaemon"] = "stopped"
    assert agent.main(["paseo-daemon", "--profile", "pi-ollama"]) == 0
    assert json.loads(marker.read_text())["profile"] == "pi-ollama"
    env = ran[-1][2]
    assert env["OLLAMA_API_KEY"] == keys["ollama-key"] and "CLAUDE_CODE_OAUTH_TOKEN" not in env
    assert "acme-agent-agent.ollama-key" in env["LOCAL_REVIEW_COMMAND"]
    said = capsys.readouterr().out
    assert "inference profile pi-ollama, with the keys of pi-ollama" in said and "[profiles.NAME] cells" in said


@pytest.mark.parametrize("code,stdout", [(1, "{}"), (0, "broken JSON"), (0, "[]")])
def test_paseo_status_refuses_invalid_replies(monkeypatch, code, stdout):
    monkeypatch.setattr(agent.Sh, "out", lambda self, cmd: subprocess.CompletedProcess(cmd, code, stdout))
    with pytest.raises(agent.Refused, match="cannot read the Paseo daemon's status"):
        agent.paseo_status(agent.Sh(), "/path/to/paseo")


def test_a_failed_paseo_start_does_not_record_a_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, "paseo_status", lambda sh, cli: {"localDaemon": "stopped"})
    monkeypatch.setattr(agent.Sh, "tty", lambda *args, **kwargs: 12)
    assert agent.start_paseo(agent.Sh(), tmp_path, ["claude"], {}) == 12
    assert not (tmp_path / agent.PASEO_PROFILE).exists()


def test_a_paseo_start_records_the_digest_of_the_review_settings(tmp_path, monkeypatch):
    """The record holds a digest of the profile set's entries in `local-review.json`, so the service sees a change of
    the review settings alone."""
    state = iter([{"localDaemon": "stopped"}, {"localDaemon": "running", "pid": 5, "startedAt": "t"}])
    monkeypatch.setattr(agent, "paseo_status", lambda sh, cli: next(state))
    monkeypatch.setattr(agent.Sh, "tty", lambda *args, **kwargs: 0)
    digest = agent.review_digest(["claude"])
    assert agent.start_paseo(agent.Sh(), tmp_path, ["claude"], {}, digest) == 0
    assert json.loads((tmp_path / agent.PASEO_PROFILE).read_text())["review"] == digest


def test_load_orchestrator_loads_the_installed_agent_into_the_user_domain_once(tmp_path, monkeypatch):
    """The service's LaunchAgent goes into user/UID. Each line names the unlock from the owner's account."""
    plist = tmp_path / "com.acme-agent.orchestrator.plist"
    monkeypatch.setattr(agent, "ORCHESTRATOR_PLIST", plist)
    print_cmd = ("launchctl", "print", f"user/503/{agent.ORCHESTRATOR_LABEL}")
    absent = FakeSh({})
    assert "not installed" in agent.load_orchestrator(absent, 503) and absent.calls == []
    plist.write_text("")
    loaded = FakeSh({})  # print exits 0: the job is there
    assert agent.load_orchestrator(loaded, 503) == f"the orchestrator's service: loaded already in user/503{agent.UNLOCK_NOTE}"
    assert "owlcode unlock" in agent.UNLOCK_NOTE
    assert loaded.calls == [list(print_cmd)]
    fresh = FakeSh({print_cmd: (113, "")})
    assert agent.load_orchestrator(fresh, 503) == f"the orchestrator's service: loaded in user/503{agent.UNLOCK_NOTE}"
    assert fresh.calls[-1] == ["launchctl", "bootstrap", "user/503", str(plist)]
    refused = FakeSh({print_cmd: (113, ""), ("launchctl", "bootstrap"): (5, "Input/output error")})
    line = agent.load_orchestrator(refused, 503)
    assert "not loaded (Input/output error)" in line and f"sudo launchctl bootstrap user/503 {plist}" in line


IFCONFIG_OFF = "lo0: flags=8049<UP>\n\tinet 127.0.0.1 netmask 0xff000000\nen0: flags=8863<UP>\n\tinet 192.168.1.20 netmask 0xffffff00\n"
IFCONFIG_ON = IFCONFIG_OFF + "utun4: flags=8051<UP>\n\tinet 100.113.228.95 --> 100.113.228.95 netmask 0xffffffff\n"


def test_tailnet_address_reads_only_tailscale_s_range_from_ifconfig():
    """The LAN's and the loopback's addresses are not Tailscale's."""
    assert agent.tailnet_address(FakeSh({("ifconfig",): (0, IFCONFIG_ON)})) == "100.113.228.95"
    assert agent.tailnet_address(FakeSh({("ifconfig",): (0, IFCONFIG_OFF)})) is None
    assert agent.tailnet_address(FakeSh({("ifconfig",): (1, "")})) is None


def test_wait_for_tailnet_returns_at_once_or_after_the_address_comes(capsys):
    slept: list[float] = []
    assert agent.wait_for_tailnet(FakeSh({("ifconfig",): (0, IFCONFIG_ON)}), slept.append) == "100.113.228.95"
    assert slept == [] and capsys.readouterr().out == ""

    class Later(FakeSh):
        def out(self, cmd, stdin=None, cwd=None):
            done = super().out(cmd, stdin, cwd)
            return subprocess.CompletedProcess(done.args, 0, IFCONFIG_ON if len(self.calls) >= 3 else IFCONFIG_OFF, "")

    assert agent.wait_for_tailnet(Later({}), slept.append) == "100.113.228.95"
    assert slept == [agent.TAILNET_WAIT_SECONDS] * 2 and "Waiting for Tailscale" in capsys.readouterr().out


def test_wait_for_tailnet_gives_up_after_its_tries():
    slept: list[float] = []
    sh = FakeSh({("ifconfig",): (0, IFCONFIG_OFF)})
    assert agent.wait_for_tailnet(sh, slept.append) is None
    assert len(sh.calls) == agent.TAILNET_WAIT_TRIES and len(slept) == agent.TAILNET_WAIT_TRIES - 1


def test_paseo_daemon_refuses_without_tailscale_and_starts_nothing(tmp_path, monkeypatch, capsys):
    """Before Tailscale connects, Paseo's daemon fails with EADDRNOTAVAIL and a stack trace; one line says why."""
    monkeypatch.setattr(agent.pwd, "getpwuid", lambda uid: agent.pwd.struct_passwd(
        (agent.USER, "*", 501, 20, "", str(tmp_path), "/bin/zsh")))
    monkeypatch.setattr(agent, "unlock", lambda sh: None)
    monkeypatch.setattr(agent, "load_orchestrator", lambda sh, uid: "loaded")
    monkeypatch.setattr(agent, "wait_for_tailnet", lambda sh: None)
    monkeypatch.setattr(agent.os, "execvpe", lambda *a: pytest.fail("nothing starts"))
    assert agent.main(["paseo-daemon"]) == 1
    assert agent.NO_TAILNET in capsys.readouterr().err


def test_paseo_daemon_refuses_another_account(tmp_path, monkeypatch, capsys):
    """A first try started the daemon in the owner's account."""
    monkeypatch.setattr(agent.pwd, "getpwuid", lambda uid: agent.pwd.struct_passwd(
        ("owner", "*", 501, 20, "", str(tmp_path), "/bin/zsh")))
    monkeypatch.setattr(agent, "unlock", lambda sh: pytest.fail("no keychain in another account"))
    monkeypatch.setattr(agent.os, "execvpe", lambda *a: pytest.fail("nothing runs"))
    assert agent.main(["paseo-daemon"]) == 1
    assert f"run it as {agent.USER}" in capsys.readouterr().err


def test_session_hands_claude_its_own_arguments(tmp_path, monkeypatch):
    """A herdr pane's `claude --resume ID` goes through the alias to `session --resume ID`."""
    ran: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(agent, "unlock", lambda sh: None)
    profiles: list[str] = []
    reviews: list[str] = []
    folders: list[Path] = []
    monkeypatch.setattr(agent, "session_env", lambda sh, home, environ, profile, review_profile: profiles.append(profile) or reviews.append(review_profile) or {})
    monkeypatch.setattr(agent.os, "chdir", folders.append)

    envs: list[dict[str, str]] = []

    def execve(path, argv, env):  # the real one never returns: the fake stops too, before any other branch of main
        ran.append((path, argv))
        envs.append(env)
        raise SystemExit(0)

    monkeypatch.setattr(agent.os, "execve", execve)
    monkeypatch.setattr(agent, "keychain_get", lambda sh, name: pytest.fail("no keychain in a test"))
    for argv in (["session", "--resume", "abc", "-p", "hi"], ["session"], ["session", "--profile", "pi-ollama", "--model", "glm-5.3"]):
        with pytest.raises(SystemExit):
            agent.main(argv)
    assert [argv for _, argv in ran] == [["claude", "--resume", "abc", "-p", "hi"], ["claude"], ["pi", "--model", "glm-5.3"]]
    assert profiles == ["claude", "claude", "pi-ollama"]
    assert agent.main(["session", "--profile", "ollama"]) == 1 and len(ran) == 3
    assert [Path(path).name for path, _ in ran] == ["claude", "claude", "pi"]
    assert len(folders) == 3
    with pytest.raises(SystemExit):
        agent.main(["session", "--profile", "claude", "--keep-cwd", "--print", "start"])
    assert profiles[-1] == "claude" and ran[-1][1] == ["claude", "--print", "start"]
    assert len(folders) == 3  # A detached run keeps the product's launch folder.
    with pytest.raises(SystemExit):  # A mixed start's cell reviews on the profile in force
        agent.main(["session", "--profile", "pi-ollama", "--review-profile", "claude", "--model", "glm-5.3"])
    assert (profiles[-1], reviews[-1], ran[-1][1]) == ("pi-ollama", "claude", ["pi", "--model", "glm-5.3"])
    assert reviews[:3] == ["claude", "claude", "pi-ollama"]  # no option: the session's own profile
    with pytest.raises(SystemExit):  # OWL-34: the orchestrator's share of pytest workers reaches the harness's environment
        agent.main(["session", "--profile", "claude", "--workers", "7", "--keep-cwd", "--print", "start"])
    assert ran[-1][1] == ["claude", "--print", "start"] and envs[-1]["PYTEST_XDIST_AUTO_NUM_WORKERS"] == "7"
    assert "PYTEST_XDIST_AUTO_NUM_WORKERS" not in envs[0]  # a start with no option leaves the variable out



# A harness that no profile ships: an OpenAI-style CLI, so the tests see that the table, not Claude Code's
# names, decides the install, the session command, the key's setup and the provider's variables.
FAKE_HARNESS = agent.Harness("fakeagent", "9.8.7", "install-fakeagent {version}", ("openai",), "OPENAI_BASE_URL",
                             ("OPENAI_API_KEY", "OPENAI_BASE_URL"),
                             (("ollama-key", ("login", "--print"), "Fake: sign in"),), ".fakeagent/config.json",
                             (("telemetry", False),))
FAKE_PROFILE = agent.Profile("fake", "openai", "https://api.example/v1", "ollama-key", "OPENAI_API_KEY",
                             (("FAKEAGENT_MODEL", "m-1"),), "", "fakeagent -p --model {model}{budget}")


def own_profile(monkeypatch, review_file, name, profile, model="review-model"):
    """A test's own profile: the settings file names its local review's model too, or no session of it starts."""
    monkeypatch.setitem(agent.PROFILES, name, profile)
    read = json.loads(review_file.read_text())
    read[name] = {"model": model, "effort": "high"}
    review_file.write_text(json.dumps(read))


@pytest.mark.parametrize("name", sorted(agent.PROFILES))
def test_each_profile_starts_its_harness_with_its_provider_and_key(tmp_path, name):
    """A profile names its harness, its API style (one the harness talks), its base URL, and the variable that
    carries its key; `session` starts that harness's CLI with them, and the caller's provider variables never get through."""
    profile = agent.PROFILES[name]
    harness = agent.HARNESSES[profile.harness]
    assert profile.api in harness.apis
    assert agent.session_command(tmp_path, name, ["--resume", "x"]) == (
        str(tmp_path / ".local" / "bin" / harness.binary), [harness.binary, "--resume", "x"])
    caller = {"PATH": "/usr/bin", **{var: "caller's" for var in agent.CALLER_KEYS}}
    keychain_file = tmp_path / "agent.keychain-db"
    env = agent.session_env(FakeSh(keychain({profile.key: "the-profile-s-key-0123456789", "linear-key": "lin_api_y"})),
                            tmp_path, caller, name)
    assert env["LINEAR_API_KEY"] == "lin_api_y"
    assert dict(agent.provider_env(profile)).items() <= env.items()
    if profile.provider:  # pi's own sign-in keeps the credential in ~/.pi/agent/auth.json
        assert (profile.key, profile.variable) == ("", ""), "no item to fetch and no variable to set"
        assert agent.review_command(profile, keychain_file, REVIEW[name], name) == (
            "PI_OFFLINE=1 PI_TELEMETRY=0 pi -p --no-session --no-approve --no-extensions --no-mcp --tools read,grep,find,ls "
            "--model openai-codex/gpt-6.1-sol --thinking high")
    else:
        assert profile.key in {k for k, *_ in agent.KEYS}
        assert env[profile.variable] == "the-profile-s-key-0123456789"
    if profile.base_url and harness.base_url:  # pi reads it from its models.json (`pi_provider`)
        assert env[harness.base_url] == profile.base_url
    assert "caller's" not in env.values(), "no key or provider variable of the caller reaches a run"


def test_the_shipped_profiles_keep_their_harness_and_provider():
    assert set(agent.HARNESSES) == {"claude-code", "pi"} and agent.DEFAULT_PROFILE == "claude"
    claude = agent.PROFILES["claude"]
    assert (claude.harness, claude.api, claude.base_url, claude.variable) == ("claude-code", "anthropic", "", "CLAUDE_CODE_OAUTH_TOKEN")
    assert agent.provider_env(claude) == ()
    assert "ollama" not in agent.PROFILES
    assert set(agent.CATALOG["providers"]["ollama"]["connections"]) == {"pi"}


def test_the_caller_s_provider_variables_go_for_every_harness():
    """CALLER_KEYS holds each harness's base-URL variable, each profile's key and provider variables, and the
    OpenAI-style ones that pi reads, so a caller's key never waits for their entries."""
    for harness in agent.HARNESSES.values():
        assert {harness.base_url, *harness.provider_variables} - {""} <= set(agent.CALLER_KEYS)
    assert "" not in agent.CALLER_KEYS, "pi reads no base-URL variable"
    # Pi's key variables, and the folder whose models.json names its providers
    assert {"OPENAI_API_KEY", "AZURE_OPENAI_BASE_URL", "GEMINI_API_KEY", "PI_CODING_AGENT_DIR"} <= set(agent.HARNESSES["pi"].provider_variables)
    # Claude Code's own credential, address and provider-switch variables (its env-vars page)
    assert {"CLAUDE_CODE_USE_BEDROCK", "AWS_BEARER_TOKEN_BEDROCK", "ANTHROPIC_CUSTOM_HEADERS", "ANTHROPIC_FOUNDRY_API_KEY",
            "ANTHROPIC_VERTEX_BASE_URL", "CLAUDE_CODE_USE_VERTEX"} <= set(agent.HARNESSES["claude-code"].provider_variables)
    for profile in agent.PROFILES.values():
        # a profile on its harness's own sign-in names no variable at all
        assert {profile.variable, *(var for var, _ in profile.env)} - {""} <= set(agent.CALLER_KEYS)
    assert [name for name, p in agent.PROFILES.items() if not (p.key or p.variable)] == ["pi-chatgpt"]
    assert {"OPENAI_API_KEY", "OPENAI_BASE_URL", "ANTHROPIC_API_KEY", "OLLAMA_API_KEY"} <= set(agent.CALLER_KEYS)
    assert len(agent.CALLER_KEYS) == len(set(agent.CALLER_KEYS))


def test_session_starts_the_profile_s_harness_from_the_table(tmp_path, monkeypatch, review_file):
    """A profile on another harness starts that harness's CLI, with its provider in that harness's variables,
    and with the caller's OpenAI-style key and address gone."""
    monkeypatch.setitem(agent.HARNESSES, "fake", FAKE_HARNESS)
    own_profile(monkeypatch, review_file, "fake", FAKE_PROFILE)
    assert agent.session_command(tmp_path, "fake", ["-c"]) == (str(tmp_path / ".local" / "bin" / "fakeagent"), ["fakeagent", "-c"])
    sh = FakeSh(keychain({"ollama-key": "abc123def456.ollama-key-value", "linear-key": "lin_api_y"}))
    env = agent.session_env(sh, tmp_path, {"PATH": "/usr/bin", "OPENAI_API_KEY": "sk-caller", "OPENAI_BASE_URL": "https://caller.example"}, "fake")
    assert env["OPENAI_API_KEY"] == "abc123def456.ollama-key-value" and env["OPENAI_BASE_URL"] == "https://api.example/v1"
    assert env["FAKEAGENT_MODEL"] == "m-1" and "ANTHROPIC_BASE_URL" not in env and "CLAUDE_CODE_OAUTH_TOKEN" not in env
    ran: list[tuple[str, list[str], dict[str, str]]] = []
    monkeypatch.setattr(agent, "unlock", lambda sh: None)
    monkeypatch.setattr(agent, "keychain_get", lambda sh, name: {"ollama-key": "abc123def456.ollama-key-value", "linear-key": "lin_api_y"}.get(name))
    monkeypatch.setattr(agent.os, "chdir", lambda path: None)
    monkeypatch.setattr(agent.pwd, "getpwuid", lambda uid: agent.pwd.struct_passwd(
        (agent.USER, "*", 501, 20, "", str(tmp_path), "/bin/zsh")))

    def execve(path, argv, env):
        ran.append((path, argv, env))
        raise SystemExit(0)

    monkeypatch.setattr(agent.os, "execve", execve)
    with pytest.raises(SystemExit):
        agent.main(["session", "--profile", "fake", "--model", "m-2"])
    [(path, argv, env)] = ran
    assert (path, argv) == (str(tmp_path / ".local" / "bin" / "fakeagent"), ["fakeagent", "--model", "m-2"])
    assert env["OPENAI_BASE_URL"] == "https://api.example/v1"
    assert env["LOCAL_REVIEW_COMMAND"].endswith(" fakeagent -p --model review-model"), "the settings file's model"


def test_the_pi_command_starts_pi_for_a_pi_profile_only(tmp_path, monkeypatch, capsys):
    """Herdr types `pi ARGS` into a pane, so the pane's `pi` alias runs `agent_user.py pi`: a pi profile's session with
    the mix's review, and a refusal (never Claude Code) for no profile or a Claude Code profile."""
    ran: list[tuple[str, list[str], dict[str, str]]] = []
    monkeypatch.setattr(agent, "unlock", lambda sh: None)
    monkeypatch.setattr(agent, "keychain_get", lambda sh, name: "k-" + name)
    monkeypatch.setattr(agent.os, "chdir", lambda path: None)
    monkeypatch.setattr(agent.pwd, "getpwuid", lambda uid: agent.pwd.struct_passwd(
        (agent.USER, "*", 501, 20, "", str(tmp_path), "/bin/zsh")))
    monkeypatch.setattr(agent.os, "execve", lambda path, argv, env: (ran.append((path, argv, env)), (_ for _ in ()).throw(SystemExit(0))))
    with pytest.raises(SystemExit):
        agent.main(["pi", "--profile", "pi-lithosai", "--review-profile", "claude", "--model", "m", "--thinking", "high", "go"])
    [(path, argv, env)] = ran
    assert (path, argv) == (str(tmp_path / ".local" / "bin" / "pi"), ["pi", "--model", "m", "--thinking", "high", "go"])
    assert "claude" in env["LOCAL_REVIEW_COMMAND"], "the review of the profile in force, not the cell's"
    for bad in (["--model", "m"], ["--profile", "claude", "go"]):
        assert agent.main(["pi", *bad]) == 1 and len(ran) == 1, bad
    assert "pi takes --profile NAME of a pi profile: pi-ollama, pi-chatgpt, pi-lithosai" in capsys.readouterr().err


def test_user_installs_each_harness_at_its_version_and_writes_its_settings(tmp_path, monkeypatch):
    """`user` installs each harness's CLI that ~/.local/bin lacks, with the entry's command at its pinned version,
    and writes the entry's settings; a CLI that is there at its pin is not installed again."""
    monkeypatch.setitem(agent.HARNESSES, "fake", FAKE_HARNESS)
    home = agent_with_keys(tmp_path)  # mise, claude and pi are there, fakeagent is not
    sh = FakeSh({**keychain({name: "x" for name, *_ in agent.KEYS}), **pins(home)})
    agent.user(sh, NoAsk(), home, "acme-agent", replace=False)
    installs = [c for c in sh.calls if c[:2] == ["sh", "-c"]]
    assert installs == [["sh", "-c", "install-fakeagent 9.8.7"]], "only the missing CLI, at its version"
    assert json.loads((home / ".fakeagent" / "config.json").read_text()) == {"telemetry": False}
    assert json.loads((home / ".claude" / "settings.json").read_text()) == {"skipDangerousModePermissionPrompt": True}
    (home / ".local" / "bin" / "claude").unlink()
    sh = FakeSh({**keychain({name: "x" for name, *_ in agent.KEYS}), **pins(home)})
    agent.user(sh, NoAsk(), home, "acme-agent", replace=False)
    assert ["sh", "-c", "curl -fsSL https://claude.ai/install.sh | bash -s 2.1.328"] in sh.calls, "Claude Code at its pinned version"


def test_user_installs_a_harness_again_when_its_version_is_not_the_pin(tmp_path):
    """Pi does not update itself (`PI_OFFLINE=1` turns its own check off), so a CLI that an agent user already
    had never reached a new pin. `user` compares the binary's version with the entry's and installs it again when they
    differ; a CLI that updates itself (Claude Code) is installed only when it is missing."""
    home = agent_with_keys(tmp_path)
    pi = str(home / ".local" / "bin" / "pi")
    claude = str(home / ".local" / "bin" / "claude")
    stale = FakeSh({**keychain({name: "x" for name, *_ in agent.KEYS}), **pins(home), (pi, "--version"): (0, "0.9.9\n"),
                    (claude, "--version"): (0, "9.9.9\n")})
    agent.user(stale, NoAsk(), home, "acme-agent", replace=False)
    installs = [c for c in stale.calls if c[:2] == ["sh", "-c"]]
    assert installs == [["sh", "-c", agent.HARNESSES["pi"].install.format(version=agent.HARNESSES["pi"].version)]], \
        "pi at another version is installed again, and Claude Code's own update is left alone"
    assert [claude, "--version"] not in stale.calls, "a CLI that updates itself is not compared"
    at_pin = FakeSh({**keychain({name: "x" for name, *_ in agent.KEYS}), **pins(home),
                     (pi, "--version"): (0, "pi " + agent.HARNESSES["pi"].version + "\n")})
    agent.user(at_pin, NoAsk(), home, "acme-agent", replace=False)
    assert not [c for c in at_pin.calls if c[:2] == ["sh", "-c"]], "pi is at its pin: nothing is installed"


def test_a_key_that_a_harness_s_cli_makes_runs_its_command_before_the_prompt(tmp_path, monkeypatch):
    """Claude Code's entry runs `claude setup-token` before the prompt for the Claude token; any harness's entry
    does the same for its own item, from its own CLI."""

    class Typed(agent.Ask):
        def secret(self, prompt):
            return "sk-ant-oat01-" + "a" * 40

    have = {name: "x" for name, *_ in agent.KEYS if name != "claude-token"}
    missing = {**keychain(have), ("security", "find-generic-password", "-a", "acme-agent", "-s", "acme-agent-agent.claude-token"): (44, "")}
    sh = FakeSh(missing)
    agent.ask_keys(sh, Typed(), replace=False)
    claude = str(Path.home() / ".local" / "bin" / "claude")
    assert sh.calls.index([claude, "setup-token"]) < sh.calls.index(["security", "-i"])
    monkeypatch.setitem(agent.HARNESSES, "fake", FAKE_HARNESS)
    have = {name: "x" for name, *_ in agent.KEYS if name != "ollama-key"}
    sh = FakeSh({**keychain(have), ("security", "find-generic-password", "-a", "acme-agent", "-s", "acme-agent-agent.ollama-key"): (44, "")})
    agent.ask_keys(sh, Typed(), replace=False)
    fake = [str(Path.home() / ".local" / "bin" / "fakeagent"), "login", "--print"]
    assert sh.calls.index(fake) < sh.calls.index(["security", "-i"]), "the other harness's CLI, with its own arguments"
    assert [claude, "setup-token"] not in sh.calls, "only the harness that makes the missing item"
    sh = FakeSh(keychain({name: "x" for name, *_ in agent.KEYS}))
    agent.ask_keys(sh, Typed(), replace=False)
    assert fake not in sh.calls and [claude, "setup-token"] not in sh.calls, "a stored key runs no setup"


PHONE = "ssh-ed25519 BBBB heeler-iphone"


class Says(agent.Ask):
    """Answers each question with the next of its answers, and records the questions."""

    def __init__(self, *answers):
        self.answers, self.asked = list(answers), []

    def plain(self, prompt):
        self.asked.append(prompt)
        return self.answers.pop(0)


def admin_with_keys(tmp_path, had, ask):
    owner_home = folder(tmp_path / "owner", 0o700)
    home = folder(tmp_path / "acme-agent", 0o700)
    sh = FakeSh({**membership(agents=True), ("sudo", "-u", "owner", "-H", "cat"): (0, "ssh-ed25519 AAAA acme-agent-agent\n"),
                 ("sudo", "-u", "acme-agent", "-H", "cat", str(home / ".ssh" / "authorized_keys")): (0, had)})
    notes = agent.admin(sh, "owner", owner_home, home, "x", folder(tmp_path / "sshd_config.d", 0o755), ask=ask)
    written = sh.stdins[sh.calls.index(["sudo", "-u", "acme-agent", "-H", "/usr/bin/tee", str(home / ".ssh" / "authorized_keys")])]
    assert_root_writes_no_file_in(home, sh.calls)
    return written, notes


HERDR_LINE = f"{agent.KEY_OPTIONS} ssh-ed25519 AAAA acme-agent-agent\n"
BOOTSTRAP = "restrict,command=\"'/n/node' '/p/pair-accept.js' --pairing-id 7\" ssh-ed25519 CCCC herdr-pairing:7:exp:1793750978"


def test_admin_keeps_a_phones_key_only_with_the_owners_yes(tmp_path):
    """The agent owns its authorized_keys, so a key there waits for the owner's yes; a yes keeps it with
    the tailnet's from= and records it where the agent cannot write; a duplicate of herdr's key goes."""
    had = f"{HERDR_LINE}# a comment\nssh-ed25519 AAAA acme-agent-agent\n{PHONE}\n"
    ask = Says("y")
    written, _ = admin_with_keys(tmp_path, had, ask)
    assert written == HERDR_LINE + f'from="100.64.0.0/10,fd7a:115c:a1e0::/48",restrict,port-forwarding,pty {PHONE}\n'
    assert len(ask.asked) == 1 and "heeler-iphone" in ask.asked[0] and agent.fingerprint(PHONE) in ask.asked[0]
    assert (tmp_path / "acme-agent-phones").read_text() == "ssh-ed25519 BBBB\n"
    written, _ = admin_with_keys(tmp_path, written, NoAsk())  # approved: no second question
    assert PHONE in written
    written, _ = admin_with_keys(tmp_path, HERDR_LINE, NoAsk())  # The owner removed the phone's line
    assert written == HERDR_LINE and (tmp_path / "acme-agent-phones").read_text() == ""
    ask = Says("")
    admin_with_keys(tmp_path, f"{HERDR_LINE}{PHONE}\n", ask)  # the agent puts it back: forgotten, so asked again
    assert len(ask.asked) == 1


def test_admin_drops_a_key_the_owner_did_not_approve_and_every_pairing_key(tmp_path):
    """No is the default; a pairing's one-time key (its secret was in a QR code) never becomes a login key."""
    written, notes = admin_with_keys(tmp_path, f"{HERDR_LINE}{BOOTSTRAP}\n{PHONE}\n", Says(""))
    assert written == HERDR_LINE
    assert not (tmp_path / "acme-agent-phones").exists()
    assert sum("Removed from the agent's authorized_keys" in n for n in notes) == 2
    assert any("one-time" in n for n in notes)


def test_check_takes_a_phones_key_only_when_approved_with_the_tailnets_options(tmp_path):
    """An approved key with the tailnet's from= passes; a plain line, a key nobody approved and a pairing's
    one-time key fail, each named."""
    home = folder(tmp_path / "acme-agent", 0o700)
    replies = guards_ok(home)
    phones = tmp_path / "phones"
    phones.write_text("ssh-ed25519 BBBB\n")
    keys = home / ".ssh" / "authorized_keys"
    base = keys.read_text()
    owner_home = folder(tmp_path / "owner", 0o700)
    keys.write_text(base + f"{agent.PHONE_OPTIONS} {PHONE}\n")
    assert all(line.status == "ok" for line in agent.check(FakeSh(replies), "owner", owner_home, home, phones))
    for extra, named in ((f"{PHONE}\n", "heeler-iphone"), (f"{agent.PHONE_OPTIONS} ssh-ed25519 DDDD stranger\n", "stranger"),
                         (f"{BOOTSTRAP}\n", "herdr-pairing")):
        keys.write_text(base + f"{agent.PHONE_OPTIONS} {PHONE}\n" + extra)
        lines = agent.check(FakeSh(replies), "owner", owner_home, home, phones)
        failed = {line.name: line.detail for line in lines if line.status == "FAIL"}
        assert list(failed) == ["the agent's keys: herdr's, and the phones you approved"] and named in failed[next(iter(failed))], lines


def test_check_fails_when_sshd_lets_the_agent_in_from_the_home_lan(tmp_path):
    """The owner's call is the tailnet only; a LAN range in AllowUsers is an open door."""
    home = folder(tmp_path / "acme-agent", 0o700)
    replies = guards_ok(home) | {SSHD_T: (0, SSHD_OK + "allowusers acme-agent@192.168.1.0/24\n")}
    lines = agent.check(FakeSh(replies), "owner", folder(tmp_path / "owner", 0o700), home)
    failed = {line.name: line.detail for line in lines if line.status == "FAIL"}
    assert list(failed) == ["sshd: the agent only, with a key, no listening or agent forwarding"] and "192.168.1.0/24" in failed["sshd: the agent only, with a key, no listening or agent forwarding"], lines


def test_the_agents_node_is_the_projects():
    """Heeler's plugin runs on the agent's global Node: the same version as mise.toml's pin."""
    assert f'node = "{agent.NODE_VERSION}"' in (REPO / "mise.toml").read_text()


def test_check_fails_when_another_user_can_write_herdr(tmp_path):
    """The owner's herdr runs the agent's ~/.local/bin/herdr; a file the group or others can write is no guard."""
    home = folder(tmp_path / "acme-agent", 0o700)
    replies = guards_ok(home)
    (home / ".local" / "bin" / "herdr").chmod(0o775)
    lines = agent.check(FakeSh(replies), "owner", folder(tmp_path / "owner", 0o700), home)
    assert [line.name for line in lines if line.status == "FAIL"] == ["herdr is the agent's own file"], lines
    (home / ".local" / "bin" / "herdr").chmod(0o755)
    (home / ".local" / "bin").rename(home / ".local" / "elsewhere")
    (home / ".local" / "bin").symlink_to(home / ".local" / "elsewhere")
    failed = [line for line in agent.check(FakeSh(replies), "owner", folder(tmp_path / "owner", 0o700), home) if line.status == "FAIL"]
    assert [line.name for line in failed] == ["herdr is the agent's own file"] and "bin is a link" in failed[0].detail, failed


def test_check_fails_when_herdr_belongs_to_another_user(tmp_path, monkeypatch):
    """A ~/.local/bin/herdr that another user owns, with a closed mode, is not the agent's."""
    home = folder(tmp_path / "acme-agent", 0o700)
    replies = guards_ok(home)
    herdr = home / ".local" / "bin" / "herdr"
    real_stat = Path.stat

    def stat(self, *args, **kwargs):
        found = real_stat(self, *args, **kwargs)
        if self != herdr:
            return found
        fields = list(found)
        fields[4] = found.st_uid + 1  # st_uid
        return os.stat_result(fields)

    monkeypatch.setattr(Path, "stat", stat)
    lines = agent.check(FakeSh(replies), "owner", folder(tmp_path / "owner", 0o700), home)
    assert [line.name for line in lines if line.status == "FAIL"] == ["herdr is the agent's own file"], lines


def paseo_app(tmp_path: Path) -> Path:
    app = folder(tmp_path / "Paseo.app", 0o755) / "paseo"
    app.write_text("#!/bin/sh\n")
    app.chmod(0o755)
    agent.PASEO_APP = app  # the autouse fixture puts it back
    return app


def pins(home: Path) -> dict[tuple[str, ...], tuple[int, str]]:
    """The version replies of the CLIs that stay at their pin (pi), each at its pin: `user` installs none."""
    return {(str(home / ".local" / "bin" / h.binary), "--version"): (0, h.version + "\n")
            for h in agent.HARNESSES.values() if not h.updates_itself}


def agent_with_keys(tmp_path: Path) -> Path:
    home = folder(tmp_path / "acme-agent", 0o700)
    for tool in ("mise", "claude", "pi"):
        folder(home / ".local" / "bin", 0o700)
        (home / ".local" / "bin" / tool).write_text("")
    folder(home / "Library" / "Keychains", 0o700)
    (home / "Library" / "Keychains" / "agent.keychain-db").write_text("")
    return home


def test_user_links_paseo_to_the_app_and_removes_the_npm_copy(tmp_path):
    """The agent's paseo is the app's CLI, so the app's update updates both accounts; the npm copy goes."""
    app = paseo_app(tmp_path)
    home = agent_with_keys(tmp_path)
    old = home / ".local" / "bin" / "paseo"
    old.symlink_to(tmp_path / "an-old-npm-paseo")
    mise = str(home / ".local" / "bin" / "mise")
    npm = [mise, "exec", "--", "npm"]
    sh = FakeSh(keychain({name: "x" for name, *_ in agent.KEYS}))  # npm ls finds the package (exit 0)
    notes = agent.user(sh, NoAsk(), home, "acme-agent", replace=False)
    assert os.readlink(old) == str(app)
    uninstall = [*npm, "uninstall", "-g", "@getpaseo/cli"]
    assert uninstall in sh.calls and sh.calls[sh.calls.index(uninstall) + 1] == [mise, "reshim"], sh.calls
    assert any("Removed the npm copy" in n for n in notes), notes
    sh = FakeSh({**keychain({name: "x" for name, *_ in agent.KEYS}), (*npm, "ls"): (1, "")})
    notes = agent.user(sh, NoAsk(), home, "acme-agent", replace=False)  # again: no npm copy, the link stays
    assert uninstall not in sh.calls and os.readlink(old) == str(app)
    assert not any("npm" in n for n in notes)


def test_user_without_the_paseo_app_makes_no_link(tmp_path):
    home = agent_with_keys(tmp_path)
    sh = FakeSh({**keychain({name: "x" for name, *_ in agent.KEYS}), **pins(home)})
    notes = agent.user(sh, NoAsk(), home, "acme-agent", replace=False)
    assert not os.path.lexists(home / ".local" / "bin" / "paseo")
    assert not any("npm" in " ".join(c) for c in sh.calls), "no app: nothing is removed"
    assert any(n.startswith("No Paseo app at ") for n in notes), notes


def paseo_check(tmp_path: Path, app_v: str, cli_v: str, daemon: dict | None, linked: bool = True) -> list:
    app = paseo_app(tmp_path)
    home = folder(tmp_path / "acme-agent", 0o700)
    replies = guards_ok(home)
    link = home / ".local" / "bin" / "paseo"
    link.symlink_to(app if linked else tmp_path / "npm-paseo")
    as_agent = ("sudo", "-u", "acme-agent", "-H")
    replies |= {(*as_agent, str(app), "--version"): (0, f"{app_v}\n"), (*as_agent, str(link), "--version"): (0, f"{cli_v}\n"),
                (*as_agent, str(link), "daemon", "status", "--json"): (0, json.dumps(daemon or {"localDaemon": "stopped", "pid": None}))}
    sh = FakeSh(replies)
    lines = agent.check(sh, "owner", folder(tmp_path / "owner", 0o700), home)
    assert not any("stop" in c for c in sh.calls), "the check never stops or restarts the daemon"
    return [line for line in lines if line.name.startswith("paseo")]


def test_check_shows_the_three_paseo_versions_when_they_match(tmp_path):
    lines = paseo_check(tmp_path, "0.16.1", "0.16.1", {"localDaemon": "running", "daemonVersion": "0.16.1"})
    assert [(l.status, l.name) for l in lines] == [("ok", "paseo is the app's CLI"), ("ok", "paseo: app 0.16.1, CLI 0.16.1, daemon 0.16.1")]


def test_check_names_the_restart_when_the_daemon_runs_another_version(tmp_path):
    lines = paseo_check(tmp_path, "0.16.1", "0.16.1", {"localDaemon": "running", "daemonVersion": "0.15.4"})
    failed = [l for l in lines if l.status == "FAIL"]
    assert [l.name for l in failed] == ["paseo: app 0.16.1, CLI 0.16.1, daemon 0.15.4"]
    assert "paseo daemon stop" in failed[0].detail and "paseo-daemon" in failed[0].detail and "no agent running" in failed[0].detail


def test_check_takes_a_daemon_that_does_not_run_as_a_line_not_a_fail(tmp_path):
    lines = paseo_check(tmp_path, "0.16.1", "0.16.1", None)
    assert [(l.status, l.name) for l in lines][-1] == ("ok", "paseo: app 0.16.1, CLI 0.16.1, daemon not running")


def test_check_fails_when_the_agent_s_paseo_is_not_the_app_s(tmp_path):
    lines = paseo_check(tmp_path, "0.16.1", "0.15.4", {"localDaemon": "running", "daemonVersion": "0.15.4"}, linked=False)
    assert [(l.status, l.name) for l in lines] == [("FAIL", "paseo is the app's CLI"), ("FAIL", "paseo: app 0.16.1, CLI 0.15.4, daemon 0.15.4")]
    assert "run `user` again" in lines[1].detail


def test_check_fails_when_paseo_prints_no_version(tmp_path):
    """Two unread versions are not equal versions: the line fails and names the cause, with no restart step."""
    lines = paseo_check(tmp_path, "", "", {"localDaemon": "running", "daemonVersion": "0.16.1"})
    failed = [l for l in lines if l.status == "FAIL" and l.name.startswith("paseo: app")]
    assert [l.name for l in failed] == ["paseo: app unknown, CLI unknown, daemon 0.16.1"]
    assert "could not read paseo's version" in failed[0].detail and "restart" not in failed[0].detail


def test_check_fails_when_the_daemon_s_version_is_unread(tmp_path):
    """A running daemon with no readable version is its own FAIL, with no restart step (a restart kills agents)."""
    for daemon in ({"localDaemon": "running"}, {"localDaemon": "running", "daemonVersion": "dev"}):
        lines = paseo_check(tmp_path, "0.16.1", "0.16.1", daemon)
        failed = [l for l in lines if l.status == "FAIL" and l.name.startswith("paseo: app")]
        assert [l.name for l in failed] == ["paseo: app 0.16.1, CLI 0.16.1, daemon unknown"]
        assert "could not read the daemon's version" in failed[0].detail and "restart" not in failed[0].detail
        tmp_path = tmp_path / "again"
        tmp_path.mkdir()


def test_check_without_the_paseo_app_compares_nothing(tmp_path):
    home = folder(tmp_path / "acme-agent", 0o700)
    sh = FakeSh(guards_ok(home))
    lines = agent.check(sh, "owner", folder(tmp_path / "owner", 0o700), home)
    assert [l.status for l in lines if l.name.startswith("paseo")] == ["ok"]
    assert not any("--version" in c or "daemon" in c for c in sh.calls)


def _repos_line(tmp_path, repos):
    home = folder(tmp_path / "acme-agent", 0o700)
    gh = (*GH, str(home / "agent_user.py"), "gh", "api")
    replies = {**guards_ok(home), (*gh, "/installation/repositories"): (0, json.dumps(repos))}
    lines = agent.check(FakeSh(replies), "owner", folder(tmp_path / "owner", 0o700), home)
    return next(line for line in lines if line.name == "the app sees the loop's repositories only")


def test_the_app_may_see_each_repository_of_the_loop(tmp_path):
    assert _repos_line(tmp_path, ["maurolopes/acme-tools", "maurolopes/acme-loop", "maurolopes/acme-agent"]).status == "ok"


def test_a_repository_outside_the_loop_fails_and_is_named(tmp_path):
    line = _repos_line(tmp_path, [*LOOP, "maurolopes/other"])
    assert line.status == "FAIL" and "not in the loop: maurolopes/other" in line.detail


def test_a_missing_repository_fails_and_is_named(tmp_path):
    line = _repos_line(tmp_path, ["maurolopes/acme-agent"])
    assert line.status == "FAIL" and "missing: maurolopes/acme-loop, maurolopes/acme-tools" in line.detail


def test_the_check_reads_the_clone_s_table_as_the_agent_and_a_failed_read_wants_the_agents_repository_only(tmp_path):
    home = folder(tmp_path / "acme-agent", 0o700)
    sh = FakeSh({**guards_ok(home), CAT: (1, "")})
    lines = agent.check(sh, "owner", folder(tmp_path / "owner", 0o700), home)
    assert [*CAT, str(home / "acme-agent" / "AGENTS.md")] in sh.calls, "as the agent, never root's own read"
    line = next(line for line in lines if line.name == "the app sees the loop's repositories only")
    assert line.status == "FAIL" and "not in the loop: maurolopes/acme-loop, maurolopes/acme-tools" in line.detail
    assert str(home / "acme-agent" / "AGENTS.md") in line.detail


def test_pi_s_entry_installs_its_pinned_package_under_local_and_removes_a_copy_in_mise_s_node():
    """Pi is npm's package, installed with mise's Node under ~/.local, so ~/.local/bin/pi outlives a new Node; a
    copy that `npm install -g` put in mise's Node goes first (an interactive shell and Paseo would find it first)."""
    pi = agent.HARNESSES["pi"]
    assert (pi.binary, pi.base_url, pi.key_setup, pi.settings_file) == ("pi", "", (), "")
    assert re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", pi.version), "a pinned version, never `latest`"
    install = pi.install.format(version=pi.version)
    assert install.startswith(f'"$HOME/.local/bin/mise" exec node@{agent.NODE_VERSION} -- sh -c ')
    assert install.index(f"npm uninstall -g {agent.PI_PACKAGE}") < install.index(
        f'npm install -g --prefix "$HOME/.local" {agent.PI_PACKAGE}@{pi.version}')
    assert (pi.trust_file, pi.provider_file) == (".pi/agent/trust.json", ".pi/agent/models.json")


def test_provider_check_refuses_stale_custom_entries_and_leaves_built_in_providers_to_pi(tmp_path):
    path = tmp_path / agent.HARNESSES["pi"].provider_file
    custom = {"baseUrl": "https://old.example", "models": []}
    built_in = {"models": [{"id": "keep"}]}
    assert not agent.check_provider(tmp_path, "pi-ollama")
    agent.write_json(path, {"pi-ollama": custom, "openai-codex": built_in}, "providers")
    assert not agent.check_provider(tmp_path, "pi-ollama")
    assert agent.check_provider(tmp_path, "pi-chatgpt")
    assert json.loads(path.read_text())["providers"] == {"openai-codex": built_in, "pi-ollama": custom}
    agent.write_json(path, {"pi-ollama": agent.pi_provider(agent.PROFILES["pi-ollama"])}, "providers")
    assert agent.check_provider(tmp_path, "pi-ollama")


class CloningSh(FakeSh):
    """A FakeSh whose `git clone` makes the clone with the AGENTS.md of TEAMS_MD, as GitHub's copy holds it."""

    def tty(self, cmd, cwd=None, env=None):
        if list(cmd[:2]) == ["git", "clone"]:
            Path(cmd[-1]).mkdir(parents=True)
            (Path(cmd[-1]) / "AGENTS.md").write_text(TEAMS_MD)
        return super().tty(cmd, cwd, env)


def test_the_first_user_run_trusts_every_clone_of_the_teams_since_it_writes_the_trust_after_the_clone(tmp_path):
    home = agent_with_keys(tmp_path)
    assert not (home / "acme-agent").exists(), "a new agents' user: no clone yet"
    agent.user(CloningSh(keychain({name: "x" for name, *_ in agent.KEYS})), NoAsk(), home, "acme-agent", replace=False)
    trust = json.loads((home / ".pi" / "agent" / "trust.json").read_text())
    assert trust == {str(home / "acme-agent"): True, str(home / "acme-loop"): True, str(home / "acme-tools"): True}


def test_user_writes_pi_s_trust_of_the_clones_and_each_pi_profile_s_provider(tmp_path):
    """A run cannot answer pi's trust question, and without trust pi loads neither `.agents/skills` (the skill
    `implement`) nor `.pi/` (the guards' adapter): `user` trusts the agents' clones, and no other folder. Each
    pi profile is a provider of pi's models.json, under the profile's name, with the key as its variable, never the value,
    except one on pi's own provider (`pi-chatgpt`): pi's catalog holds that one, and its entry here would replace
    it. LithosAI's entry carries its models as its docs state them. Other entries stay, and a second run writes
    nothing."""
    home = agent_with_keys(tmp_path)
    folder(home / "acme-agent", 0o700)
    (home / "acme-agent" / "AGENTS.md").write_text(TEAMS_MD)
    (home / ".local" / "bin" / "pi").unlink()
    sh = FakeSh(keychain({name: "x" for name, *_ in agent.KEYS}))
    notes = agent.user(sh, NoAsk(), home, "acme-agent", replace=False)
    assert ["sh", "-c", agent.HARNESSES["pi"].install.format(version=agent.HARNESSES["pi"].version)] in sh.calls
    assert any("pi-chatgpt runs on pi's own provider openai-codex" in n and "pi-signin" in n for n in notes), notes
    trust = json.loads((home / ".pi" / "agent" / "trust.json").read_text())
    assert trust == {str(home / "acme-agent"): True, str(home / "acme-loop"): True, str(home / "acme-tools"): True}
    models = json.loads((home / ".pi" / "agent" / "models.json").read_text())
    assert set(models["providers"]) == {"pi-ollama", "pi-lithosai"}
    catalog = agent.CATALOG
    for name, entry in models["providers"].items():
        provider = catalog["providers"][catalog["profiles"][name]["provider"]]
        connection = provider["connections"]["pi"]
        assert (entry["baseUrl"], entry["api"], entry["apiKey"]) == (
            connection["base_url"], "openai-completions", f"${connection['key_variable']}")
        listed = {m["id"]: m for m in entry["models"]}
        assert set(listed) == {model for model, data in provider["models"].items() if "pi" in data["efforts"]}
        for model, settings in listed.items():
            data = provider["models"][model]
            assert settings["reasoning"] is True
            assert settings["thinkingLevelMap"] == catalog["efforts"][data["efforts"]["pi"]]
            assert settings.get("contextWindow") == data["context_window"]
            assert settings.get("maxTokens") == data["max_output"]
    # LithosAI's entry as its docs state it, its key as the variable that a session sets, never the value
    lithosai = models["providers"]["pi-lithosai"]
    assert (lithosai["baseUrl"], lithosai["api"], lithosai["apiKey"]) == (
        "https://api.lithosai.cloud/v1", "openai-completions", "$LITHOSAI_API_KEY")
    listed = {m["id"]: m for m in lithosai["models"]}
    assert set(listed) == {"deepseek-ai/DeepSeek-V4.1-Flash", "deepseek-ai/DeepSeek-V4.1-Flash-fast",
                           "deepseek-ai/DeepSeek-V4.1-Flash-ultra", "zai-org/GLM-5.3", "zai-org/GLM-5.3-Flash",
                           "zai-org/GLM-5.3-Flash-ultra"}, "a tier is its own ID, the same model at other rates"
    assert {m: (e["contextWindow"], e["maxTokens"]) for m, e in listed.items()} == {
        "deepseek-ai/DeepSeek-V4.1-Flash": (1048576, 393216), "deepseek-ai/DeepSeek-V4.1-Flash-fast": (1048576, 393216),
        "deepseek-ai/DeepSeek-V4.1-Flash-ultra": (1048576, 393216), "zai-org/GLM-5.3": (1048576, 131072),
        "zai-org/GLM-5.3-Flash": (1048576, 131072), "zai-org/GLM-5.3-Flash-ultra": (1048576, 131072)}
    assert listed["deepseek-ai/DeepSeek-V4.1-Flash"]["compat"] == {
        "thinkingFormat": "deepseek", "supportsReasoningEffort": True, "maxTokensField": "max_tokens"}
    assert listed["deepseek-ai/DeepSeek-V4.1-Flash"]["thinkingLevelMap"]["medium"] is None, "no `medium` on DeepSeek"
    assert listed["zai-org/GLM-5.3"]["thinkingLevelMap"] == {
        "minimal": None, "low": "low", "medium": None, "high": "high", "xhigh": None, "max": "max"}
    mine = {"providers": {"local": {"baseUrl": "http://localhost:11434/v1"}, "pi-ollama": {"baseUrl": "https://old.example"}},
            "x": 1}
    (home / ".pi" / "agent" / "models.json").write_text(json.dumps(mine))
    (home / ".pi" / "agent" / "trust.json").write_text(json.dumps({"/tmp/elsewhere": False}))
    agent.user(FakeSh(keychain({name: "x" for name, *_ in agent.KEYS})), NoAsk(), home, "acme-agent", replace=False)
    models = json.loads((home / ".pi" / "agent" / "models.json").read_text())
    assert models["x"] == 1 and models["providers"]["local"] == {"baseUrl": "http://localhost:11434/v1"}
    assert models["providers"]["pi-ollama"]["baseUrl"] == "https://ollama.com/v1", "the profile's entry is the table's"
    assert json.loads((home / ".pi" / "agent" / "trust.json").read_text())["/tmp/elsewhere"] is False
    before = (home / ".pi" / "agent" / "models.json").stat().st_mtime_ns
    agent.user(FakeSh(keychain({name: "x" for name, *_ in agent.KEYS})), NoAsk(), home, "acme-agent", replace=False)
    assert (home / ".pi" / "agent" / "models.json").stat().st_mtime_ns == before, "already set: not written again"
    (home / ".pi" / "agent" / "models.json").write_text(json.dumps({"providers": [1]}))
    with pytest.raises(agent.Refused, match=r"models\.json is not a JSON object with an object providers"):
        agent.user(FakeSh(keychain({name: "x" for name, *_ in agent.KEYS})), NoAsk(), home, "acme-agent", replace=False)


def test_pi_signin_says_whether_pi_holds_a_sign_in_and_never_reads_a_token(tmp_path):
    """The orchestrator's `key_check` of a profile on pi's own provider runs `pi-signin`: it tests that
    ~/.pi/agent/auth.json holds that provider's entry, and only that. A token's value never leaves the file, and a file
    that others can read fails, as does one that pi wrote while it was read (`/login` is the owner's)."""
    home = folder(tmp_path / "agent", 0o700)
    folder(home / ".pi" / "agent", 0o700)
    auth = home / ".pi" / "agent" / "auth.json"
    assert agent.pi_sign_in(home, "openai-codex") == [
        agent.Line(agent.FAIL, f"pi sign-in: no openai-codex sign-in in {auth} (pi's /login as {agent.USER})")]
    token = "eyJhbGciOi.token-value-never-printed"
    auth.write_text(json.dumps({"openai-codex": {"type": "oauth", "access": token, "refresh": token}}))
    auth.chmod(0o600)
    assert agent.pi_sign_in(home, "openai-codex") == [agent.Line(agent.OK, "pi sign-in: openai-codex is signed in")]
    assert token not in repr(agent.pi_sign_in(home, "openai-codex"))
    assert agent.pi_sign_in(home, "openai")[0][0] == agent.FAIL, "another provider is not this one"
    auth.chmod(0o644)
    [(level, name, detail)] = agent.pi_sign_in(home, "openai-codex")
    assert level == agent.FAIL and "open to others" in name and detail == f"chmod 600 {auth}"
    auth.write_text("{ half a file")
    [(level, name, detail)] = agent.pi_sign_in(home, "openai-codex")
    assert level == agent.FAIL and "no openai-codex sign-in" in name and "JSONDecodeError" in detail
    auth.write_text(json.dumps({"openai-codex": "a string entry"}))
    assert agent.pi_sign_in(home, "openai-codex")[0][0] == agent.FAIL


def test_pi_signin_says_when_another_account_cannot_read_the_file(tmp_path, monkeypatch):
    """The sign-in is the agents' user's, and `key_check` runs as whoever runs the orchestrator. Another account
    gets a line that names the account to use, not a traceback (round 1's R1-2): on the `/usr/bin/python3` that a profile's
    `key_check` names (3.9 on the Mac) `Path.is_file()` raises `PermissionError` through a home that the account cannot
    enter, since pathlib does not ignore EACCES there; and `os.access` answers no for a file that it can see and not read."""
    home = folder(tmp_path / "agent", 0o700)
    auth = home / ".pi" / "agent" / "auth.json"
    auth.parent.mkdir(parents=True)
    auth.write_text(json.dumps({"openai-codex": {"type": "oauth"}}))
    auth.chmod(0o600)
    def is_file(self, *args, **kwargs):  # what `/usr/bin/python3` (3.9) does through a home that it cannot enter
        if self == auth:
            raise PermissionError(13, "Permission denied", str(self))
        return real_is_file(self, *args, **kwargs)

    real_is_file = Path.is_file
    monkeypatch.setattr(Path, "is_file", is_file)
    [(level, name, detail)] = agent.pi_sign_in(home, "openai-codex")
    assert level == agent.FAIL and "not readable here" in name and "acme-agent" in name and detail == ""
    monkeypatch.undo()
    monkeypatch.setattr(agent.os, "access", lambda path, mode: False)
    assert agent.pi_sign_in(home, "openai-codex") == [
        agent.Line(agent.FAIL, f"pi sign-in: {auth} is not readable here (run this as {agent.USER})")]


def test_pi_signin_waits_for_a_file_that_pi_is_writing(tmp_path, monkeypatch):
    """Pi rewrites auth.json under its own lock, so a read at that moment can catch half of it: a first failed
    read is tried once more before the sign-in is called missing."""
    home = folder(tmp_path / "agent", 0o700)
    auth = home / ".pi" / "agent" / "auth.json"
    auth.parent.mkdir(parents=True)
    auth.write_text(json.dumps({"openai-codex": {"type": "oauth"}}))
    auth.chmod(0o600)

    reads = []

    def read_text(self, *args, **kwargs):
        reads.append(self)
        return "{ half a file" if len(reads) == 1 else json.dumps({"openai-codex": {"type": "oauth"}})

    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(agent.time, "sleep", lambda seconds: None)
    assert agent.pi_sign_in(home, "openai-codex") == [agent.Line(agent.OK, "pi sign-in: openai-codex is signed in")]
    assert len(reads) == 2, "the second read is the one that counts"


def test_a_pi_ollama_session_gets_its_key_as_the_variable_that_models_json_names(tmp_path):
    """Pi reads Ollama's address from its models.json, so no base-URL variable is set; the key reaches pi as
    OLLAMA_API_KEY (the entry's `$OLLAMA_API_KEY`), pi's own start-up requests stay off, and no Claude token gets through."""
    profile = agent.PROFILES["pi-ollama"]
    assert (profile.harness, profile.api, profile.base_url, profile.variable) == ("pi", "openai", "https://ollama.com/v1", "OLLAMA_API_KEY")
    assert agent.pi_provider(profile)["apiKey"] == f"${profile.variable}"
    sh = FakeSh(keychain({"claude-token": "sk-ant-oat-x", "linear-key": "lin_api_y", "ollama-key": "abc123def456.ollama-key-value"}))
    caller = {"PATH": "/usr/bin", "OPENAI_API_KEY": "theirs", "PI_CODING_AGENT_DIR": "/tmp/theirs", "OLLAMA_API_KEY": "theirs"}
    env = agent.session_env(sh, tmp_path, caller, "pi-ollama")
    assert env["OLLAMA_API_KEY"] == "abc123def456.ollama-key-value" and env["PI_OFFLINE"] == "1" and env["PI_TELEMETRY"] == "0"
    assert not {"OPENAI_API_KEY", "PI_CODING_AGENT_DIR", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_BASE_URL"} & env.keys()
    assert agent.session_command(tmp_path, "pi-ollama", ["--model", "pi-ollama/glm-5.3"]) == (
        str(tmp_path / ".local" / "bin" / "pi"), ["pi", "--model", "pi-ollama/glm-5.3"])


def test_a_pi_lithosai_session_gets_lithosai_s_key_and_no_other_provider_s(tmp_path):
    """The profile `pi-lithosai` sends every call to LithosAI with the agents' own key, which reaches pi as the
    LITHOSAI_API_KEY that its models.json entry names; no other provider's key gets through, and the key is one that
    `user` may leave out (an empty answer stores nothing)."""
    profile = agent.PROFILES["pi-lithosai"]
    assert (profile.harness, profile.api, profile.base_url, profile.key, profile.variable) == (
        "pi", "openai", "https://api.lithosai.cloud/v1", "lithosai-key", "LITHOSAI_API_KEY")
    assert profile.key in agent.OPTIONAL_KEYS and agent.pi_provider(profile)["apiKey"] == "$LITHOSAI_API_KEY"
    sh = FakeSh(keychain({"claude-token": "sk-ant-oat-x", "linear-key": "lin_api_y", "ollama-key": "ollama-values-0123456789",
                          "lithosai-key": "lithosai-values-0123456789"}))
    caller = {"PATH": "/usr/bin", **{var: "theirs" for var in agent.CALLER_KEYS}}
    env = agent.session_env(sh, tmp_path, caller, "pi-lithosai")
    assert env["LITHOSAI_API_KEY"] == "lithosai-values-0123456789" and env["PI_OFFLINE"] == "1" and env["PI_TELEMETRY"] == "0"
    assert not {"OLLAMA_API_KEY", "OPENAI_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_AUTH_TOKEN"} & env.keys()
    assert "theirs" not in env.values()
    assert "-s acme-agent-agent.lithosai-key -w" in env["LOCAL_REVIEW_COMMAND"]
    assert ("--model pi-lithosai/" + REVIEW["pi-lithosai"][0] + " --thinking " + REVIEW["pi-lithosai"][1]
            in env["LOCAL_REVIEW_COMMAND"])
    without = FakeSh(keychain({"claude-token": "sk-ant-oat-x", "linear-key": "lin_api_y"}))
    with pytest.raises(agent.Refused, match="the lithosai-key is not in the Keychain"):
        agent.session_env(without, tmp_path, caller, "pi-lithosai")


def test_the_lithosai_cells_use_the_efforts_that_its_models_run_and_never_medium():
    """LithosAI's own efforts are `low`, `high` and `max`, and `medium` maps to nothing on DeepSeek-V4.1-Flash
    and to `max` on the GLM models, so no run cell names it; the review runs on the owner's model at the effort that its
    page makes the default, on another family than the cells."""
    table = tomllib.loads((REPO / "orchestrator" / "orchestrator.example.toml").read_text())["profiles"]["pi-lithosai"]
    models = {m.id: dict(m.settings) for m in agent.PROFILES["pi-lithosai"].models}
    efforts = {cell: table[cell][1] for cell in ("neither", "intricate", "judgment", "both")}
    assert set(efforts.values()) <= {"low", "high", "max"}
    runs = {table[cell][0] for cell in efforts}
    for cell, effort in efforts.items():
        for model in runs:
            assert models[model]["thinkingLevelMap"][effort] == effort, f"{cell} runs {model} at an effort it does not run"
    assert (table["review_model"], table["review_effort"]) == ("zai-org/GLM-5.3-Flash", "max"), "the owner's choice"
    review = models[table["review_model"]]
    assert review["thinkingLevelMap"][table["review_effort"]] == table["review_effort"]
    assert models["deepseek-ai/DeepSeek-V4.1-Flash"]["thinkingLevelMap"]["medium"] is None, "no `medium` on DeepSeek"
    for model in runs:
        assert model.split("-", 1)[0] != table["review_model"].split("-", 1)[0], "the review runs on another family"


def test_pi_s_local_review_reads_the_prompt_with_read_only_tools_and_no_file_that_needs_trust(tmp_path):
    """Pi's print mode with only its read-only tools, none of the files that need trust (`--no-approve`: .pi/
    and .agents/skills; AGENTS.md still loads), no MCP server, no saved session; the model is PROVIDER/MODEL, the provider being the profile's name
    in models.json. The key is read at run time, never written into the text; no budget flag, which counts Anthropic's prices."""
    keychain_file = tmp_path / "agent.keychain-db"
    command = agent.review_command(agent.PROFILES["pi-ollama"], keychain_file, REVIEW["pi-ollama"], "pi-ollama")
    assert command == ("PI_OFFLINE=1 PI_TELEMETRY=0 OLLAMA_API_KEY=$(/usr/bin/security find-generic-password -a acme-agent -s "
                       f"acme-agent-agent.ollama-key -w {keychain_file}) pi -p --no-session --no-approve --no-extensions --no-mcp "
                       "--tools read,grep,find,ls --model pi-ollama/deepseek-v4.1-flash --thinking high")
    sh = FakeSh(keychain({"linear-key": "lin_api_y", "ollama-key": "abc123def456.ollama-key-value"}))
    env = agent.session_env(sh, tmp_path, {"PATH": "/usr/bin"}, "pi-ollama")
    assert env["LOCAL_REVIEW_COMMAND"] == agent.review_command(
        agent.PROFILES["pi-ollama"], tmp_path / "Library" / "Keychains" / "agent.keychain-db", REVIEW["pi-ollama"], "pi-ollama")


def test_pi_review_loads_the_installed_metrics_extension(tmp_path):
    import shlex

    extension = tmp_path / agent.METRICS_EXTENSION
    extension.touch()
    command = agent.review_command(agent.PROFILES["pi-ollama"], tmp_path / "Library/Keychains/agent.keychain-db",
                                   REVIEW["pi-ollama"], "pi-ollama")
    assert {"--no-approve", "--no-extensions"} <= set(shlex.split(command))
    assert shlex.split(command)[-2:] == ["--extension", str(extension)]


def test_a_pi_profile_lists_every_model_that_its_runs_and_its_review_use():
    """Pi gives thinking levels only to a model that its models.json lists (an unlisted one runs with a warning
    and no `--thinking`, measured 2026-11-21 at 1.5.2): each pi profile lists the models of its orchestrator cells, and the
    review uses one of those catalog models. A profile on pi's own catalog (`openai-codex`) carries
    no models.json entry at all: its models are
    pi's, `PROVIDER/MODEL`, and an entry under that provider would replace the catalog's context window."""
    tables = tomllib.loads((REPO / "orchestrator" / "orchestrator.example.toml").read_text())["profiles"]
    pis = [name for name, p in agent.PROFILES.items() if p.harness == "pi"]
    assert pis == ["pi-ollama", "pi-chatgpt", "pi-lithosai"]
    chatgpt = agent.PROFILES["pi-chatgpt"]
    assert (chatgpt.provider, chatgpt.models) == ("openai-codex", ()), "pi's own catalog holds its provider and models"
    entries = {path.name: keys for path, under, keys in agent.harness_files(Path("/tmp/agent"), "pi") if under == "providers"}
    assert entries == {"models.json": {"pi-ollama": agent.pi_provider(agent.PROFILES["pi-ollama"]),
                                       "pi-lithosai": agent.pi_provider(agent.PROFILES["pi-lithosai"])}}, \
        "a catalog provider keeps the models that pi itself lists"
    for name in pis:
        cells = {tables[name][cell][0] for cell in ("neither", "intricate", "judgment", "both")}
        assert tables[name]["harness"] == "pi"
        ids = cells | {tables[name]["review_model"]}
        if agent.PROFILES[name].provider:
            assert ids <= agent.CATALOG["providers"][tables[name]["provider"]]["models"].keys()
        else:
            assert ids <= {m.id for m in agent.PROFILES[name].models}


def paseo_daemon_run(tmp_path, monkeypatch, keys, profile):
    """Run `paseo-daemon --profile PROFILE` with a fake daemon that starts at once: the exit code, the environment that the
    daemon got (None when it never started) and the record of its profiles (None when there is none)."""
    started: list[dict[str, str]] = []
    status = {"localDaemon": "stopped"}
    monkeypatch.setattr(agent, "unlock", lambda sh: None)
    monkeypatch.setattr(agent, "load_orchestrator", lambda sh, uid: "loaded")
    monkeypatch.setattr(agent, "wait_for_tailnet", lambda sh: "100.113.228.95")
    monkeypatch.setattr(agent, "keychain_get", lambda sh, name: keys.get(name))
    monkeypatch.setattr(agent.pwd, "getpwuid", lambda uid: agent.pwd.struct_passwd(
        (agent.USER, "*", 501, 20, "", str(tmp_path), "/bin/zsh")))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setattr(agent.Sh, "out", lambda self, cmd: subprocess.CompletedProcess(cmd, 0, json.dumps(status)))

    def start(sh, argv, cwd=None, env=None):
        started.append(env)
        status.update(localDaemon="running", pid=7, startedAt="t")
        return 0

    monkeypatch.setattr(agent.Sh, "tty", start)
    monkeypatch.setattr(agent, "profile_in_force", lambda: profile)
    code = agent.main(["paseo-daemon", "--profile", profile])
    marker = tmp_path / agent.PASEO_PROFILE
    return code, (started[0] if started else None), (json.loads(marker.read_text()) if marker.exists() else None)


MIX_KEYS = {"claude-token": "sk-ant-oat-x", "lithosai-key": "lithos-key-value-0123456789", "linear-key": "lin_api_y"}


def test_a_mixed_profile_gives_the_daemon_the_keys_of_every_profile_its_cells_name(tmp_path, monkeypatch, review_file, capsys):
    """`paseo-daemon --profile pi-lithosai` holds pi-lithosai's key and Claude Code's, because the cells of
    pi-lithosai run on both; the local review stays pi-lithosai's, and the record names both profiles, the first one first."""
    table = json.loads(review_file.read_text())
    table["pi-lithosai"]["cells"] = ["pi-lithosai", "claude"]
    review_file.write_text(json.dumps(table))
    code, env, marker = paseo_daemon_run(tmp_path, monkeypatch, MIX_KEYS, "pi-lithosai")
    assert code == 0
    assert env["LITHOSAI_API_KEY"] == MIX_KEYS["lithosai-key"] and env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat-x"
    assert env["LINEAR_API_KEY"] == "lin_api_y" and "acme-agent-agent.lithosai-key" in env["LOCAL_REVIEW_COMMAND"]
    assert "claude-token" not in env["LOCAL_REVIEW_COMMAND"], "one review model for the mix: the profile in force's"
    assert marker == {"profile": "pi-lithosai", "profiles": ["pi-lithosai", "claude"], "pid": 7, "startedAt": "t",
                      "review": agent.review_digest(["pi-lithosai", "claude"])}
    assert "with the keys of pi-lithosai, claude" in capsys.readouterr().out


def test_a_profile_that_a_cell_names_needs_its_key_in_the_keychain_and_a_missing_one_starts_nothing(tmp_path, monkeypatch, review_file, capsys):
    table = json.loads(review_file.read_text())
    table["pi-lithosai"]["cells"] = ["pi-lithosai", "claude"]
    review_file.write_text(json.dumps(table))
    keys = {k: v for k, v in MIX_KEYS.items() if k != "claude-token"}
    code, env, marker = paseo_daemon_run(tmp_path, monkeypatch, keys, "pi-lithosai")
    assert code == 1 and env is None and marker is None
    assert "the claude-token is not in the Keychain" in capsys.readouterr().err


def test_two_profiles_that_set_one_variable_to_two_values_start_no_daemon(tmp_path, monkeypatch, review_file, capsys):
    """An agent inherits one environment, so a variable that two profiles of the daemon set differently cannot hold both."""
    table = json.loads(review_file.read_text())
    table["pi-lithosai"]["cells"] = ["pi-lithosai", "pi-ollama"]
    review_file.write_text(json.dumps(table))
    monkeypatch.setitem(agent.PROFILES, "pi-ollama", agent.PROFILES["pi-ollama"]._replace(env=(("PI_OFFLINE", "0"),)))
    keys = {**MIX_KEYS, "ollama-key": "abc123def456.ollama-key-value"}
    code, env, marker = paseo_daemon_run(tmp_path, monkeypatch, keys, "pi-lithosai")
    assert code == 1 and env is None and marker is None
    assert "the profiles pi-lithosai and pi-ollama set PI_OFFLINE to two values" in capsys.readouterr().err
    monkeypatch.setitem(agent.PROFILES, "pi-ollama", agent.PROFILES["pi-ollama"]._replace(env=agent.PROFILES["pi-lithosai"].env))
    code, env, _ = paseo_daemon_run(tmp_path, monkeypatch, keys, "pi-lithosai")
    assert code == 0, "one value for both: no conflict"
    assert env["OLLAMA_API_KEY"] == keys["ollama-key"] and env["LITHOSAI_API_KEY"] == keys["lithosai-key"]


def test_a_provider_variable_that_one_profile_sets_and_another_of_its_harness_leaves_unset_starts_no_daemon(tmp_path, monkeypatch, review_file, capsys):
    """A base URL that only one Claude Code profile sets would reach the agent of the other, with its token."""
    table = json.loads(review_file.read_text())
    table["claude"]["cells"] = ["claude", "claude-other"]
    table["claude-other"] = table["claude"]
    review_file.write_text(json.dumps(table))
    other = agent.PROFILES["claude"]._replace(base_url="https://other.example", key="other-key", variable="ANTHROPIC_AUTH_TOKEN")
    monkeypatch.setitem(agent.PROFILES, "claude-other", other)
    keys = {**MIX_KEYS, "other-key": "other-key-value-0123456789"}
    code, env, marker = paseo_daemon_run(tmp_path, monkeypatch, keys, "claude")
    assert code == 1 and env is None and marker is None
    assert "the profile claude-other sets ANTHROPIC_BASE_URL and claude does not" in capsys.readouterr().err


def test_the_profiles_of_the_cells_come_from_the_settings_file_and_a_bad_list_starts_nothing(tmp_path, review_file):
    assert agent.profile_set("pi-ollama") == ["pi-ollama"], "a file from before the cells has no cells"
    table = json.loads(review_file.read_text())
    table["pi-lithosai"]["cells"] = ["pi-lithosai", "claude"]
    review_file.write_text(json.dumps(table))
    assert agent.profile_set("pi-lithosai") == ["pi-lithosai", "claude"]
    for bad in (["claude"], ["pi-lithosai", "gone"], ["pi-lithosai", "claude", "claude"], "claude", []):
        table["pi-lithosai"]["cells"] = bad
        review_file.write_text(json.dumps(table))
        with pytest.raises(agent.Refused, match="cells of pi-lithosai name no list of profiles that starts with pi-lithosai"):
            agent.profile_set("pi-lithosai")


def test_a_profile_on_its_harnesss_own_sign_in_adds_no_key_to_the_daemon_and_asks_no_keychain_item(tmp_path, monkeypatch, review_file):
    """pi-chatgpt keeps its sign-in in pi: it has no key, so the daemon of a mix with it reads no item for it."""
    table = json.loads(review_file.read_text())
    table["pi-lithosai"]["cells"] = ["pi-lithosai", "pi-chatgpt"]
    review_file.write_text(json.dumps(table))
    asked: list[str] = []
    keys = dict(MIX_KEYS)
    code, env, marker = paseo_daemon_run(tmp_path, monkeypatch, keys, "pi-lithosai")
    monkeypatch.setattr(agent, "keychain_get", lambda sh, name: asked.append(name) or keys.get(name))
    assert agent.own_variables(agent.Sh(), "pi-chatgpt") == dict(agent.provider_env(agent.PROFILES["pi-chatgpt"]))
    assert asked == [], "no keychain item for a sign-in profile"
    assert code == 0 and marker["profiles"] == ["pi-lithosai", "pi-chatgpt"]
    assert env["LITHOSAI_API_KEY"] == keys["lithosai-key"] and "CLAUDE_CODE_OAUTH_TOKEN" not in env


def test_user_removes_an_opencode_that_an_earlier_user_installed(tmp_path):
    """OpenCode was dropped. `user` uninstalls its package from ~/.local and deletes its config folder, and an
    agent that never had OpenCode gets neither step."""
    home = agent_with_keys(tmp_path)
    config = folder(home / ".config" / "opencode", 0o700)
    (config / "opencode.json").write_text("{}")
    (home / ".local" / "bin" / "opencode").write_text("")
    sh = FakeSh({**keychain({name: "x" for name, *_ in agent.KEYS}), **pins(home)})
    agent.user(sh, NoAsk(), home, "acme-agent", replace=False)
    removals = [c for c in sh.calls if c[:2] == ["sh", "-c"] and "npm uninstall -g --prefix" in c[2]]
    assert len(removals) == 1 and removals[0][2].endswith("opencode-ai")
    assert not config.exists()
    clean = agent_with_keys(tmp_path / "clean")
    sh = FakeSh({**keychain({name: "x" for name, *_ in agent.KEYS}), **pins(clean)})
    agent.user(sh, NoAsk(), clean, "acme-agent", replace=False)
    assert not [c for c in sh.calls if "npm uninstall" in " ".join(c)]


def test_a_session_takes_its_pytest_workers_from_the_option() -> None:
    """The orchestrator's `{workers_flag}` is ` --workers N`: the session sets PYTEST_XDIST_AUTO_NUM_WORKERS from it (OWL-34)."""
    assert agent.take_workers(["--workers", "7", "--keep-cwd", "--print"]) == (7, ["--keep-cwd", "--print"])
    assert agent.take_workers(["--keep-cwd"]) == (0, ["--keep-cwd"])
    for bad in (["--workers"], ["--workers", "0"], ["--workers", "x"], ["--workers", "7; rm"], ["--workers", "1000"]):
        with pytest.raises(agent.Refused, match="--workers takes"):
            agent.take_workers(bad)


def test_admin_asks_before_it_makes_a_user_and_stops_on_any_answer_but_y(tmp_path, monkeypatch):
    """OWL-44: a name from the checkout's `origin` is not always the service's agents' user."""
    monkeypatch.delenv("AGENT_USER", raising=False)
    for answer in ("n", "", "yes please"):
        owner_home = folder(tmp_path / f"owner-{len(answer)}", 0o700)
        sh = FakeSh({("dscl", ".", "-read", "/Groups/agents"): (56, ""), ("dscl", ".", "-read", "/Users/acme-agent"): (56, "")})
        ask = Says(answer)
        with pytest.raises(agent.Refused):
            agent.admin(sh, "owner", owner_home, tmp_path / "acme-agent", "x", folder(tmp_path / "sshd", 0o755), ask=ask)
        assert len(ask.asked) == 1 and "No macOS user acme-agent exists. Make it" in ask.asked[0] and "[y/N]" in ask.asked[0]
        assert "the checkout's `origin`" in ask.asked[0]
        names = {c[0] for c in sh.calls}
        assert not names & {"sysadminctl", "dseditgroup", "createhomedir", "chown", "chmod", "sudo"}, sh.calls
        assert not any(c[:2] == ["dscl", "."] and c[2] == "-create" for c in sh.calls)
        assert not (tmp_path / "acme-agent").exists()


def test_admin_question_names_agent_user_as_the_source(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_USER", "acme-agent")
    sh = FakeSh({("dscl", ".", "-read", "/Users/acme-agent"): (56, "")})
    ask = Says("n")
    with pytest.raises(agent.Refused, match=r"set \[service\] user of /usr/local/etc/orchestrator.toml .*then run owlcode admin again") as refused:
        agent.admin(sh, "owner", tmp_path, tmp_path / "acme-agent", "x", ask=ask)
    assert "AGENT_USER=NAME" not in str(refused.value)
    assert "came from AGENT_USER" in ask.asked[0] and "[service] user" in ask.asked[0]


# ---- a project's own agents' user (OWL-62, design b2) ----

ABC = agent.Project("ABC", "acme/product", 6768)
PROJECT_JSON = json.dumps(ABC._asdict())


@pytest.fixture
def abc_project(monkeypatch):
    """The agents' user of team ABC alone: its project file names the repository acme/product and the port 6768."""
    monkeypatch.setattr(agent, "PROJECT", ABC)
    monkeypatch.setattr(agent, "REPO", ABC.repo)
    monkeypatch.setattr(agent, "CLONE", Path("product"))
    return ABC


def test_a_project_s_file_is_read_in_its_own_account_only_and_a_bad_one_names_its_fault(tmp_path, monkeypatch):
    home = folder(tmp_path / "acme-agent", 0o700)
    (home / agent.PROJECT_FILE).write_text(PROJECT_JSON)
    me = {"name": agent.USER}
    monkeypatch.setattr(agent.pwd, "getpwuid", lambda uid: agent.pwd.struct_passwd(
        (me["name"], "*", uid, 600, "", str(home), "/bin/zsh")))
    assert agent.own_project(501, home) == ABC
    me["name"] = "owner"
    assert agent.own_project(501, home) is None, "root and the owner never read a file of the agent's home"
    me["name"] = agent.USER
    assert agent.own_project(501, tmp_path) is None, "no file: the user of before, for every row of the teams"
    (home / agent.PROJECT_FILE).write_text('{"team": "ABC"}')
    with pytest.raises(ValueError, match=r"repo None is not OWNER/NAME.*run `owlcode admin --project TEAM` again"):
        agent.own_project(501, home)
    for found, fault in (({"team": "abc", "repo": "acme/product"}, "not a team's key"),
                         ({"team": "ABC", "repo": "acme"}, "not OWNER/NAME"),
                         ({"team": "ABC", "repo": "acme/Product"}, "not OWNER/NAME"),
                         ({"team": "ABC", "repo": "acme/a/b"}, "not OWNER/NAME"),
                         ({"team": "ABC", "repo": "acme/product", "paseo_port": 80}, "not a port from 1024 to 65535"),
                         ({"team": "ABC", "repo": "acme/product", "paseo_port": True}, "not a port"),
                         ([], "not a JSON object")):
        with pytest.raises(ValueError, match=fault):
            agent.project_from(found)
    assert agent.project_from({"team": "ABC", "repo": "acme/product"}) == agent.Project("ABC", "acme/product", 0)


def test_a_project_s_user_has_its_repository_only(abc_project):
    assert agent.loop_repos(TEAMS_MD) == ("acme/product",), "the table of the clone names other teams: none of them"
    assert agent.loop_repos(TEAMS_MD, ABC) == ("acme/product",)


def admin_of(tmp_path, project, ifconfig="\tinet 127.0.0.1 netmask 0xff000000\n\tinet 100.101.102.103 netmask 0xffffffff\n"):
    owner_home = folder(tmp_path / "owner", 0o700)
    home = folder(tmp_path / "acme-agent", 0o700)
    sh = FakeSh({**membership(agents=True), ("dscl", ".", "-read", "/Groups/com.apple.access_ssh"): (56, ""),
                 ("sudo", "-u", "owner", "-H", "cat"): (0, "ssh-ed25519 AAAA acme-agent-agent\n"), ("ifconfig",): (0, ifconfig)})
    sshd_dir = folder(tmp_path / "sshd_config.d", 0o755)
    (sshd_dir / "000-acme-agent.conf").write_text(agent.sshd_conf())
    notes = agent.admin(sh, "owner", owner_home, home, "the script's text", sshd_dir, project=project, orchestrator_user="acme-orch")
    return sh, home, notes


def test_admin_of_a_project_writes_its_file_as_the_agent_and_prints_what_the_orchestrator_s_user_needs(tmp_path):
    sh, home, notes = admin_of(tmp_path, ABC)
    write = ["sudo", "-u", "acme-agent", "-H", "/usr/bin/tee", str(home / agent.PROJECT_FILE)]
    assert write in sh.calls and json.loads(sh.stdins[sh.calls.index(write)]) == {"team": "ABC", "repo": "acme/product", "paseo_port": 6768}
    assert_root_writes_no_file_in(home, sh.calls)
    text = "\n".join(notes)
    assert 'paseo_host = "100.101.102.103:6768"' in text and 'paseo_password_item = "acme-orch-agent.abc-paseo-password"' in text
    keychain = "/Users/acme-orch/Library/Keychains/agent.keychain-db"
    assert f"  sudo -u acme-orch -H security unlock-keychain {keychain}\n" in text
    assert text.count("security add-generic-password -U -a acme-orch -s acme-orch-agent.abc-paseo-password -w\n") == 1, \
        "-w last: security asks for the password at a hidden prompt, so no command line holds it"
    assert "herdr machine add acme-agent --label acme-agent --remote-session default" in text, "its own herdr machine"
    assert notes[-1].startswith("acme-agent is ready for team ABC (acme/product). Next, as the agent:")
    assert not any("password =" in n.lower() for n in notes), "never a password"


def test_admin_of_a_project_names_the_address_it_cannot_read_and_prints_no_paseo_line_with_no_port(tmp_path):
    _, _, notes = admin_of(tmp_path, ABC, ifconfig="\tinet 192.168.1.4 netmask 0xffffff00\n")
    assert 'paseo_host = "ADDRESS:6768"' in "\n".join(notes) and any(n.startswith("Tailscale is not connected") for n in notes)
    sh, _, notes = admin_of(tmp_path / "again", agent.Project("ABC", "acme/product"))
    assert not any("paseo" in n.lower() for n in notes) and ["ifconfig"] not in sh.calls


def test_admin_takes_the_project_from_its_options_and_refuses_a_wrong_one(tmp_path, monkeypatch, capsys):
    got = []
    monkeypatch.setattr(agent, "admin", lambda *a, **k: got.append(k) or ["done"])
    monkeypatch.setattr(agent.os, "geteuid", lambda: 0)
    monkeypatch.setenv("SUDO_USER", "owner")
    monkeypatch.setattr(agent.pwd, "getpwnam", lambda name: agent.pwd.struct_passwd((name, "*", 501, 20, "", str(tmp_path), "/bin/zsh")))
    base = ["admin", "--team", "ABC", "--repo", "acme/product"]
    assert agent.main([*base, "--paseo-port", "6768", "--orchestrator-user", "acme-orch"]) == 0
    assert got[-1]["project"] == ABC and got[-1]["orchestrator_user"] == "acme-orch"
    assert agent.main(["admin"]) == 0 and got[-1]["project"] is None, "no team: the agents' user of before"
    capsys.readouterr()
    assert agent.main(base) == 1 and "needs --orchestrator-user" in capsys.readouterr().err
    assert agent.main(["admin", "--team", "abc", "--repo", "acme/product", "--orchestrator-user", "acme-orch"]) == 1
    assert "admin: team 'abc' is not a team's key" in capsys.readouterr().err
    assert agent.main([*base, "--paseo-port", "22", "--orchestrator-user", "acme-orch"]) == 1
    assert "not a port from 1024 to 65535" in capsys.readouterr().err
    assert agent.main([*base, "--orchestrator-user", agent.USER]) == 1, "the orchestrator's own user is never a project's"
    assert "acme-agent is the orchestrator's user" in capsys.readouterr().err and len(got) == 2


def test_a_project_s_token_is_for_its_repository_only(app_key, abc_project):
    now = 1_800_000_000.0
    sh = FakeSh(keychain({"app-client-id": "Iv23liABCDEFGH", "app-key": base64.b64encode(app_key).decode()}))
    asked = []

    def api(method, path, bearer, body=None):
        asked.append((method, path, body))
        return {"id": 42} if method == "GET" else {"token": "ghs_new", "expires_at": "2027-01-15T08:00:00Z"}

    assert agent.app_token(sh, api, lambda: now) == "ghs_new"
    assert asked == [("GET", "/repos/acme/product/installation", None),
                     ("POST", "/app/installations/42/access_tokens", {"repositories": ["product"]})]


def test_fetch_sends_a_body_as_json(monkeypatch):
    import io
    sent = []

    def urlopen(req, timeout):
        sent.append(req)
        return io.BytesIO(b'{"token": "t"}')

    monkeypatch.setattr(agent.urllib.request, "urlopen", urlopen)
    assert agent.fetch("POST", "/x", "jwt", {"repositories": ["product"]}) == {"token": "t"}
    assert json.loads(sent[0].data) == {"repositories": ["product"]} and sent[0].get_header("Content-type") == "application/json"
    agent.fetch("GET", "/y", "jwt")
    assert sent[1].data is None and sent[1].get_header("Content-type") is None


def test_a_project_s_user_clones_and_trusts_its_repository_only_and_asks_for_its_paseo_password_once(tmp_path, abc_project, capsys):
    paseo_app(tmp_path)
    home = agent_with_keys(tmp_path)
    cli = str(home / ".local" / "bin" / "paseo")
    get = (cli, "daemon", "config", "get", "daemon.auth.password")
    keys = keychain({name: "x" for name, *_ in agent.KEYS})
    sh = CloningSh({**keys, **pins(home), get: (0, json.dumps({"path": "daemon.auth.password", "set": False, "value": "[redacted]"}))})
    notes = agent.user(sh, NoAsk(), home, "acme-agent", replace=False)
    assert ["git", "clone", "https://github.com/acme/product.git", str(home / "product")] in sh.calls
    assert json.loads((home / ".pi" / "agent" / "trust.json").read_text()) == {str(home / "product"): True}
    assert [cli, "daemon", "set-password"] in sh.calls, "Paseo's own hidden prompt"
    assert any("takes the new password at its next start" in n for n in notes)
    said = capsys.readouterr().out
    assert "The keys of team ABC (acme/product) only" in said and "a Linear key limited to the team ABC" in said
    sh = FakeSh({**keys, **pins(home), get: (0, json.dumps({"path": "daemon.auth.password", "set": True, "value": "[redacted]"}))})
    notes = agent.user(sh, NoAsk(), home, "acme-agent", replace=False)
    assert [cli, "daemon", "set-password"] not in sh.calls and not any("password" in n for n in notes), "set already"
    sh = FakeSh({**keys, **pins(home), get: (1, "")})
    notes = agent.user(sh, NoAsk(), home, "acme-agent", replace=False)
    assert [cli, "daemon", "set-password"] not in sh.calls and any("Could not read whether" in n for n in notes)


def test_the_user_of_before_asks_for_no_paseo_password(tmp_path):
    paseo_app(tmp_path)
    home = agent_with_keys(tmp_path)
    sh = FakeSh({**keychain({name: "x" for name, *_ in agent.KEYS}), **pins(home)})
    agent.user(sh, NoAsk(), home, "acme-agent", replace=False)
    assert not any("set-password" in c or "daemon.auth.password" in c for c in sh.calls)


def test_a_paseo_start_with_an_address_sets_it_in_paseo_s_config_first(tmp_path, monkeypatch):
    state = iter([{"localDaemon": "stopped"}, {"localDaemon": "running", "pid": 5, "startedAt": "t"}])
    monkeypatch.setattr(agent, "paseo_status", lambda sh, cli: next(state))
    sh = FakeSh({})
    assert agent.start_paseo(sh, tmp_path, ["claude"], {}, "", "100.101.102.103:6768") == 0
    cli = str(tmp_path / ".local/bin/paseo")
    assert sh.calls == [[cli, "daemon", "config", "set", "daemon.listen", "100.101.102.103:6768", "--string"], [cli, "daemon", "start"]]


def test_paseo_daemon_of_a_project_listens_on_its_port(tmp_path, monkeypatch, abc_project):
    monkeypatch.setattr(agent, "unlock", lambda sh: None)
    monkeypatch.setattr(agent, "load_orchestrator", lambda sh, uid: "loaded")
    monkeypatch.setattr(agent, "wait_for_tailnet", lambda sh: "100.113.228.95")
    monkeypatch.setattr(agent, "keychain_get", lambda sh, name: {"claude-token": "sk-ant-oat-x", "linear-key": "lin_api_y"}.get(name))
    monkeypatch.setattr(agent.pwd, "getpwuid", lambda uid: agent.pwd.struct_passwd(
        (agent.USER, "*", 501, 600, "", str(tmp_path), "/bin/zsh")))
    started = []
    monkeypatch.setattr(agent, "start_paseo", lambda sh, home, profiles, env, review="", listen="": started.append(listen) or 0)
    assert agent.main(["paseo-daemon"]) == 0 and started == ["100.113.228.95:6768"]
    monkeypatch.setattr(agent, "PROJECT", agent.Project("ABC", "acme/product"))
    assert agent.main(["paseo-daemon"]) == 0 and started[-1] == "", "no port: the daemon keeps its address"


def test_check_of_a_project_s_user_wants_the_app_on_its_repository_only(tmp_path):
    home = folder(tmp_path / "acme-agent", 0o700)
    gh = (*GH, str(home / "agent_user.py"), "gh", "api")
    replies = {**guards_ok(home), (*CAT, str(home / agent.PROJECT_FILE)): (0, PROJECT_JSON)}
    lines = agent.check(FakeSh({**replies, (*gh, "/installation/repositories"): (0, '["acme/product"]')}), "owner",
                        folder(tmp_path / "owner", 0o700), home)
    assert next(line for line in lines if line.name == "the app sees the loop's repositories only").status == "ok"
    lines = agent.check(FakeSh({**replies, (*gh, "/installation/repositories"): (0, json.dumps(["acme/product", *LOOP]))}), "owner",
                        folder(tmp_path / "owner", 0o700), home)
    line = next(line for line in lines if line.name == "the app sees the loop's repositories only")
    assert line.status == "FAIL" and "not in the loop: maurolopes/acme-agent" in line.detail
    assert "choose only acme/product, the repository of team ABC" in line.detail


def test_check_reads_only_its_own_user_s_lines_of_allowusers(tmp_path):
    """Each project's user has its own sshd file, and sshd adds up their AllowUsers lines: another user's entries
    give this one nothing. A home-LAN entry of this user still fails."""
    home = folder(tmp_path / "acme-agent", 0o700)
    other = "allowusers acme-other@127.0.0.1\nallowusers acme-other@100.64.0.0/10\n"
    lines = agent.check(FakeSh({**guards_ok(home), SSHD_T: (0, SSHD_OK + other)}), "owner", folder(tmp_path / "owner", 0o700), home)
    assert all(line.status == "ok" for line in lines), lines
    for lan in ("allowusers acme-agent@192.168.1.0/24\n", "allowusers *@192.168.1.0/24\n", "allowusers acme-*@192.168.1.0/24\n"):
        lines = agent.check(FakeSh({**guards_ok(home), SSHD_T: (0, SSHD_OK + other + lan)}), "owner", folder(tmp_path / "owner", 0o700), home)
        failed = {line.name: line.detail for line in lines if line.status == "FAIL"}
        assert list(failed) == ["sshd: the agent only, with a key, no listening or agent forwarding"], lan
        assert "192.168.1.0/24" in failed["sshd: the agent only, with a key, no listening or agent forwarding"], "a pattern that matches this user counts"


# -- the orchestrator's own GitHub App (OWL-70) ---------------------------------------------------------------------

APP_ITEM = "acme-orch-agent.orchestrator-app-key"


def app_file(folder, client_id="Iv23liORCHESTRA1", item=APP_ITEM, repos=("acme/product", "other/xyz")):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / agent.APP_FILE).write_text(json.dumps({"client_id": client_id, "key_item": item, "repos": list(repos)}))
    return folder


def http_error(path, code):
    """GitHub's answer as urllib raises it, with a body, so no file is left open."""
    import email.message
    import io
    return agent.urllib.error.HTTPError(agent.API + path, code, "Not Found", email.message.Message(), io.BytesIO(b"{}"))


def app_sh(app_key, values=None, pem_form="base64"):
    """The service's keychain: the app's key in its item (as `security -s ITEM -w` prints it), and the cached tokens."""
    stored = base64.b64encode(app_key).decode() if pem_form == "base64" else app_key.decode()
    replies = keychain(values or {})
    replies[("security", "find-generic-password", "-s", APP_ITEM, "-w")] = (0, stored + "\n")
    return FakeSh(replies)


def test_the_app_file_beside_the_installed_copy_names_the_orchestrator_s_app(tmp_path):
    assert agent.service_app(tmp_path) is None, "no file: the shim uses the implementer key, as before"
    assert agent.service_app(app_file(tmp_path / "a")) == agent.ServiceApp("Iv23liORCHESTRA1", APP_ITEM, ("acme/product", "other/xyz"))
    assert agent.service_app(app_file(tmp_path / "b", "", "")) is None, "both keys empty: as before"
    for folder, fault in ((app_file(tmp_path / "c", "Iv23liORCHESTRA1", ""), "key_item '' is not a keychain item"),
                          (app_file(tmp_path / "d", "nope"), "client_id 'nope' is not an app's client ID"),
                          (app_file(tmp_path / "e", item="-w"), "key_item '-w' is not a keychain item"),
                          (app_file(tmp_path / "f", repos=()), "repos must list each product's repository")):
        with pytest.raises(agent.Refused, match=fault):
            agent.service_app(folder)
    (tmp_path / "g").mkdir()
    (tmp_path / "g" / agent.APP_FILE).write_text("{")
    with pytest.raises(agent.Refused, match=r"cannot read .* run `owlcode restart`"):
        agent.service_app(tmp_path / "g")


@pytest.mark.parametrize(("args", "environ", "repo"), [
    (["pr", "list", "--repo", "acme/product", "--state", "open"], {}, "acme/product"),
    (["pr", "view", "7", "-R", "other/xyz"], {}, "other/xyz"),
    (["pr", "close", "7", "--repo=github.com/other/xyz"], {}, "other/xyz"),
    (["api", "repos/other/xyz", "--jq", ".id"], {}, "other/xyz"),
    (["api", "/repos/other/xyz/pulls/7"], {}, "other/xyz"),
    (["api", "--paginate", "/installation/repositories"], {}, "acme/product"),
    (["api", "/installation/repositories"], {"GH_REPO": "other/xyz"}, "other/xyz"),
])
def test_each_gh_call_finds_its_repository(args, environ, repo):
    assert agent.call_repo(args, environ, "acme/product") == repo


def test_a_call_with_no_repository_shape_is_refused():
    with pytest.raises(agent.Refused, match="is not a repository as OWNER/NAME"):
        agent.call_repo(["pr", "list", "--repo", "nope"], {}, "acme/product")


@pytest.mark.parametrize("form", ["base64", "pem"])
def test_the_orchestrator_s_app_mints_a_token_for_the_call_s_repository_and_caches_each_one(app_key, form):
    now = 1_800_000_000.0
    app = agent.ServiceApp("Iv23liORCHESTRA1", APP_ITEM, ("acme/product", "other/xyz"))
    sh = app_sh(app_key, {"app-token.acme.product": f"{int(now) + 3000}:ghs_cached", "app-token.other.xyz": f"{int(now) + 200}:ghs_old"},
                pem_form=form)
    asked = []

    def api(method, path, bearer, body=None):
        asked.append((method, path, body))
        return {"id": 77} if method == "GET" else {"token": "ghs_new", "expires_at": "2027-01-15T08:00:00Z"}

    assert agent.service_app_token(sh, app, "acme/product", api, lambda: now) == "ghs_cached" and asked == []
    assert agent.service_app_token(sh, app, "other/xyz", api, lambda: now) == "ghs_new", "under five minutes left: a new one"
    assert asked == [("GET", "/repos/other/xyz/installation", None), ("POST", "/app/installations/77/access_tokens", None)]
    ends = calendar.timegm(time.strptime("2027-01-15T08:00:00Z", "%Y-%m-%dT%H:%M:%SZ"))
    assert sh.stdins[-1].endswith(f"acme-agent-agent.app-token.other.xyz -w {ends}:ghs_new {agent.keychain_file()}\n")
    assert ["security", "find-generic-password", "-s", APP_ITEM, "-w", agent.keychain_file()] in sh.calls
    assert not any(f"{agent.SERVICE}.app-key" in c or f"{agent.SERVICE}.app-client-id" in c for c in sh.calls), "never the implementer key"


def test_a_repository_that_the_app_does_not_reach_and_a_missing_key_are_refused(app_key):
    app = agent.ServiceApp("Iv23liORCHESTRA1", APP_ITEM, ("acme/product",))

    def not_installed(method, path, bearer, body=None):
        raise http_error(path, 404)

    with pytest.raises(agent.Refused, match=r"the orchestrator's app is not installed on acme/product, or its key is not the app's .*404"):
        agent.service_app_token(app_sh(app_key), app, "acme/product", not_installed, lambda: 1_800_000_000.0)
    with pytest.raises(agent.Refused, match=f"the orchestrator's app key is not in the keychain item {APP_ITEM}"):
        agent.service_app_token(FakeSh({}), app, "acme/product", not_installed, lambda: 1_800_000_000.0)


def test_the_app_check_names_the_app_and_each_repository_it_reaches_or_not(app_key):
    app = agent.ServiceApp("Iv23liORCHESTRA1", APP_ITEM, ("acme/product", "other/xyz"))

    def api(method, path, bearer, body=None):
        assert method == "GET" and body is None, "the check mints no token"
        if path == "/app":
            return {"slug": "acme-orchestrator", "id": 5}
        if path == "/repos/other/xyz/installation":
            raise http_error(path, 404)
        return {"id": 77}

    assert agent.app_check(app_sh(app_key), app, api, lambda: 1_800_000_000.0) == [
        (agent.OK, "github: the orchestrator's app acme-orchestrator reaches acme/product"),
        (agent.FAIL, "github: the orchestrator's app acme-orchestrator is not installed on other/xyz")]
    lines = agent.app_check(FakeSh({}), app, api, lambda: 1_800_000_000.0)
    assert len(lines) == 1 and lines[0][0] == agent.FAIL and f"not in the keychain item {APP_ITEM}" in lines[0][1]


def test_the_shim_uses_the_orchestrator_s_app_when_its_file_is_there_and_the_implementer_key_else(tmp_path, monkeypatch):
    ran = []
    monkeypatch.setattr(agent, "real_gh", lambda sh: "/opt/gh")
    monkeypatch.setattr(agent.os, "execve", lambda program, argv, env: ran.append((argv, env["GH_TOKEN"])))
    monkeypatch.setattr(agent, "app_token", lambda sh: "ghs_implementer")
    monkeypatch.setattr(agent, "service_app_token", lambda sh, app, repo: f"ghs_app_for_{repo}")
    monkeypatch.setattr(agent, "HERE", tmp_path / "home-copy")
    agent.main(["gh", "pr", "list", "--repo", "other/xyz"])
    assert ran[-1] == (["/opt/gh", "pr", "list", "--repo", "other/xyz"], "ghs_implementer"), "no file beside the copy: as before"
    monkeypatch.setattr(agent, "HERE", app_file(tmp_path / "installed"))
    agent.main(["gh", "pr", "list", "--repo", "other/xyz"])
    assert ran[-1][1] == "ghs_app_for_other/xyz"
    agent.main(["gh", "api", "--paginate", "/installation/repositories"])
    assert ran[-1][1] == "ghs_app_for_acme/product", "no repository named: the first product's"


# ---- the orchestrator's own user (OWL-61, design b2) ----

ORCH_KEYCHAIN = "/Users/acme-orch/Library/Keychains/agent.keychain-db"


@pytest.fixture
def orch_user(monkeypatch):
    """The user acme-orch, as `owlcode admin --orchestrator acme-orch` names it (AGENT_USER)."""
    monkeypatch.setattr(agent, "USER", "acme-orch")
    monkeypatch.setattr(agent, "SERVICE", "acme-orch-agent")


def orch_membership() -> dict[tuple[str, ...], tuple[int, str]]:
    base = ("dsmemberutil", "checkmembership", "-U", "acme-orch", "-G")
    return {(*base, "admin"): (0, NOT_MEMBER), (*base, "agents"): (0, NOT_MEMBER), (*base, "staff"): (0, MEMBER)}


def test_admin_of_the_orchestrator_makes_a_user_with_its_keychain_only(tmp_path, orch_user, capsys):
    home = Path("/Users/acme-orch")
    sh = FakeSh({("dscl", ".", "-read", "/Users/acme-orch"): (56, ""), ("dscl", ".", "-read", "/Groups/agents"): (56, ""),
                 ("sudo", "-u", "acme-orch", "-H", "test", "-e"): (1, ""), **orch_membership()})
    ask = Says("y")
    notes = agent.admin_orchestrator(sh, home, ask, ["LINEAR_API_KEY", "DEF_LINEAR_API_KEY"],
                                     ["acme-orch-agent.abc-paseo-password", "acme-orch-agent.def-paseo-password"])
    assert len(ask.asked) == 1 and ask.asked[0].startswith("No macOS user acme-orch exists. Make it (the orchestrator's own user")
    calls = sh.calls
    add = ["sysadminctl", "-addUser", "acme-orch", "-fullName", "acme-orch orchestrator", "-password", "-"]
    for cmd in (add, ["dscl", ".", "-create", "/Users/acme-orch", "PrimaryGroupID", "600"],
                ["dseditgroup", "-o", "edit", "-a", "acme-orch", "-t", "user", "agents"],
                ["dseditgroup", "-o", "edit", "-d", "acme-orch", "-t", "user", "staff"],
                ["dscl", ".", "-create", "/Users/acme-orch", "IsHidden", "1"], ["chmod", "700", str(home)],
                ["dscl", ".", "-create", "/Users/acme-orch", "RealName", "acme-orch orchestrator"]):
        assert cmd in calls, cmd  # the full name is the marker that `owlcode check` reads (install.py ORCHESTRATOR_NAME)
    as_user = ["sudo", "-u", "acme-orch", "-H"]
    create = [*as_user, "/usr/bin/security", "create-keychain", ORCH_KEYCHAIN]
    assert calls.index(["chmod", "700", str(home)]) < calls.index(create)
    assert calls.index(create) < calls.index([*as_user, "/usr/bin/security", "set-keychain-settings", ORCH_KEYCHAIN])
    assert [*as_user, "/usr/bin/security", "default-keychain", "-d", "user", "-s", ORCH_KEYCHAIN] in calls, \
        "the default keychain: `security add-generic-password ... -w` names no file, since -w comes last"
    for cmd in calls:  # no copy of the script, no clone, no harness, no SSH login, no Paseo daemon
        assert not any(word in " ".join(cmd) for word in ("tee", "git", "ssh", "paseo", "mise", "authorized_keys")), cmd
    assert_root_writes_no_file_in(home, calls, "acme-orch")
    text = "\n".join(notes)
    print(text)
    for item in ("acme-orch-agent.linear-key", "acme-orch-agent.def-linear-key", "acme-orch-agent.abc-paseo-password",
                 "acme-orch-agent.def-paseo-password"):
        assert text.count(f"\n    sudo -u acme-orch -H security add-generic-password -U -a acme-orch -s {item} -w\n") == 1, \
            "-w last: security asks at a hidden prompt, so no command line holds the key"
    assert f"\n    sudo -u acme-orch -H security unlock-keychain {ORCH_KEYCHAIN}\n" in text
    assert "#" not in text, "a pasted comment line is an error in zsh"
    assert ('  keychain_check = "/usr/bin/security find-generic-password -a acme-orch -s acme-orch-agent.linear-key -w '
            f'{ORCH_KEYCHAIN}"') in text and '  user = "acme-orch"' in text
    assert "no paseo_password_item" not in text
    switch = ("Do not switch [service] user to acme-orch yet: the switch is part of the move, after these exist: the service's own "
              "GitHub App, the key checks in `owlcode check`, `owlcode watch`, and a way to load the service and run `owlcode "
              "unlock` with no SSH login of acme-orch.")
    assert text.index(switch) < text.index("For the move, later: these lines in the [service] table") < text.index('  user = "acme-orch"')
    assert "owlcode install" not in text, "no step that switches the service before the move"


def test_admin_of_the_orchestrator_runs_again_with_no_change_and_stops_on_a_no(tmp_path, orch_user):
    sh = FakeSh({**orch_membership(), ("dsmemberutil", "checkmembership", "-U", "acme-orch", "-G", "agents"): (0, MEMBER),
                 ("dsmemberutil", "checkmembership", "-U", "acme-orch", "-G", "staff"): (0, NOT_MEMBER)})
    notes = agent.admin_orchestrator(sh, folder(tmp_path / "acme-orch", 0o700), NoAsk())
    assert ["dscl", ".", "-create", "/Users/acme-orch", "RealName", "acme-orch orchestrator"] in sh.calls, \
        "a user made before gets the marker too"
    assert not [c for c in sh.calls if c[0] in ("dseditgroup", "sysadminctl", "createhomedir") or "create-keychain" in c]
    assert any("no paseo_password_item yet" in n for n in notes), "a command for each Paseo password, once the config names them"
    sh = FakeSh({("dscl", ".", "-read", "/Users/acme-orch"): (56, "")})
    with pytest.raises(agent.Refused, match="no macOS user acme-orch was made"):
        agent.admin_orchestrator(sh, tmp_path / "x", Says("n"))
    assert {c[0] for c in sh.calls} == {"dscl"}, "nothing changed before the yes"
    with pytest.raises(agent.Refused, match="not a keychain item's name"):
        agent.admin_orchestrator(FakeSh({}), tmp_path, NoAsk(), paseo_items=["a b"])
    with pytest.raises(agent.Refused, match="not the name of an environment variable"):
        agent.admin_orchestrator(FakeSh({}), tmp_path, NoAsk(), linear_envs=["abc"])


def test_admin_takes_the_orchestrator_s_items_from_its_options(tmp_path, monkeypatch, capsys):
    got = []
    monkeypatch.setattr(agent, "admin_orchestrator", lambda sh, home, ask, envs, items: got.append((home, envs, items)) or ["done"])
    monkeypatch.setattr(agent.os, "geteuid", lambda: 0)
    monkeypatch.setenv("SUDO_USER", "owner")
    monkeypatch.setattr(agent.pwd, "getpwnam", lambda name: agent.pwd.struct_passwd((name, "*", 501, 20, "", str(tmp_path), "/bin/zsh")))
    assert agent.main(["admin", "--orchestrator", "--linear-env", "ABC_LINEAR_API_KEY", "--paseo-item", "x.abc-paseo-password"]) == 0
    assert got == [(Path("/Users/acme-agent"), ["ABC_LINEAR_API_KEY"], ["x.abc-paseo-password"])]
    assert agent.main(["admin", "--orchestrator", "--team", "ABC", "--repo", "acme/product"]) == 1
    assert "takes no project" in capsys.readouterr().err and len(got) == 1
