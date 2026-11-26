"""bin/owlcode: each command maps to its script and arguments (OWL-54)."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BIN = REPO / "bin" / "owlcode"
_loader = importlib.machinery.SourceFileLoader("owlcode_bin", str(BIN))
_spec = importlib.util.spec_from_loader("owlcode_bin", _loader)
assert _spec
owlcode = importlib.util.module_from_spec(_spec)
_loader.exec_module(owlcode)

PY = ["/usr/bin/python3", "-I", ".workflow/approved_run.py"]
INSTALL, AGENT = "orchestrator/deploy/install.py", ".workflow/agent_user.py"


def test_each_command_maps_to_its_script_and_arguments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "orchestrator.toml"
    config.write_text('[service]\nuser = "acme-agent"\n')
    monkeypatch.setattr(owlcode, "CONFIG", config)
    argv = owlcode.argv_for
    assert argv(["install"]) == [*PY, INSTALL, "--"]
    assert argv(["install", "--config", "x.toml"]) == [*PY, INSTALL, "--", "--config", "x.toml"]
    for name in ("check", "restart", "pause", "resume", "unlock"):
        assert argv([name]) == [*PY, INSTALL, "--", name]
    assert argv(["set", "max_runs=2", "dry_run=false"]) == [*PY, INSTALL, "--", "set", "max_runs=2", "dry_run=false"]
    assert argv(["profile"]) == [*PY, INSTALL, "--", "profile"]
    assert argv(["profile", "pi-ollama"]) == [*PY, INSTALL, "--", "profile", "pi-ollama"]
    assert argv(["models"]) == [*PY, AGENT, "--", "models"]
    # models-check runs as the agents' user only: it skips the launcher
    assert argv(["models-check"]) == ["/usr/bin/python3", "-I", AGENT, "models-check"]
    assert argv(["admin"]) == ["sudo", "AGENT_USER=acme-agent", *PY, AGENT, "--", "admin"]


def test_admin_takes_the_user_from_the_service_table_only(tmp_path: Path) -> None:
    config = tmp_path / "orchestrator.toml"
    config.write_text('user = "top"\n[github]\nuser = "other"\n[service]\n# a comment\nuser = "acme-agent"  # the agents\' user\n[x]\nuser = "late"\n')
    assert owlcode.service_user(config) == "acme-agent"
    config.write_text('[github]\nuser = "other"\n')
    with pytest.raises(owlcode.Refused, match=r"no \[service\] user"):
        owlcode.service_user(config)
    with pytest.raises(owlcode.Refused, match="cannot read"):
        owlcode.service_user(tmp_path / "missing.toml")
    config.write_text('[service]\nuser = "Bad User"\n')
    with pytest.raises(owlcode.Refused, match="not a user name"):
        owlcode.service_user(config)


def test_every_command_has_a_line_in_the_list_and_the_readme() -> None:
    text = owlcode.help_text()
    readme = (REPO / "README.md").read_text()
    for name in owlcode.COMMANDS:
        assert any(line.startswith(f"owlcode {name}") for line in text.splitlines()), name
    for line in text.splitlines()[2:]:
        assert line in readme, line


def test_no_command_and_help_print_the_list_and_an_unknown_one_exits_2() -> None:
    for args in ([], ["help"]):
        ran = subprocess.run([str(BIN), *args], check=False, capture_output=True, text=True)
        assert ran.returncode == 0 and "owlcode install    " in ran.stdout and "owlcode admin" in ran.stdout, ran.stderr
    ran = subprocess.run([str(BIN), "nope"], check=False, capture_output=True, text=True)
    assert ran.returncode == 2 and "unknown command 'nope'" in ran.stderr and "owlcode install" in ran.stderr


def test_the_file_is_executable_and_runs_on_the_macs_python() -> None:
    assert BIN.stat().st_mode & 0o111
    assert BIN.read_text().startswith("#!/usr/bin/python3 -I\n")


PROJECTS = '''agent_user = "acme-abc"   # the first product's user
paseo_port = 6768
run_cwd = "~/product"
[linear]
team = "ABC"
[github]
repo = "acme/product"
[[product]]
team = "DEF"
repo = "owner/def"
areas = [
  "backend",
]
paseo_port = 6769
[[product]]
team = "XYZ"
repo = "owner/xyz"
agent_user = 'acme-xyz'   # TOML's literal string
[[product]]
team = "QRS"
repo = "owner/qrs"
agent_user = "acme-agent"
[service]
user = "acme-agent"
'''


def test_admin_of_a_project_takes_the_user_the_repository_and_the_port_from_the_team_s_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "orchestrator.toml"
    config.write_text(PROJECTS)
    monkeypatch.setattr(owlcode, "CONFIG", config)
    assert owlcode.products(config) == [
        {"agent_user": "acme-abc", "paseo_port": 6768, "team": "ABC", "repo": "acme/product"},
        {"team": "DEF", "repo": "owner/def", "paseo_port": 6769}, {"team": "XYZ", "repo": "owner/xyz", "agent_user": "acme-xyz"},
        {"team": "QRS", "repo": "owner/qrs", "agent_user": "acme-agent"}]
    tail = ["--orchestrator-user", "acme-agent"]
    assert owlcode.argv_for(["admin", "--project", "abc"]) == [
        "sudo", "AGENT_USER=acme-abc", *PY, AGENT, "--", "admin", "--team", "ABC", "--repo", "acme/product", "--paseo-port", "6768", *tail]
    for team in ("DEF", "QRS"):  # the orchestrator's own user never becomes one project's user: its tokens would reach one repository
        with pytest.raises(owlcode.Refused, match=f"the table of team {team} names no agent_user of its own: set agent_user"):
            owlcode.argv_for(["admin", "--project", team])
    assert owlcode.argv_for(["admin", "--project", "XYZ"]) == [
        "sudo", "AGENT_USER=acme-xyz", *PY, AGENT, "--", "admin", "--team", "XYZ", "--repo", "owner/xyz", *tail]
    assert owlcode.argv_for(["admin"]) == ["sudo", "AGENT_USER=acme-agent", *PY, AGENT, "--", "admin"]


@pytest.mark.parametrize(("args", "text", "fault"), [
    (["admin", "--project"], PROJECTS, "name the team"),
    (["admin", "--project", "QQQ"], PROJECTS, "has no table for the team 'QQQ': its teams are ABC, DEF, XYZ, QRS"),
    (["admin", "--project", "ABC"], PROJECTS.replace('"acme-abc"', '"Acme"'), "the agent_user 'Acme' of team ABC is not a user name"),
    (["admin", "--project", "ABC"], PROJECTS.replace("6768", "80"), "the paseo_port of team ABC must be a whole number from 1024 to 65535"),
    (["admin", "--project", "ABC"], PROJECTS.replace('[service]\nuser = "acme-agent"\n', ""), r"has no \[service\] user"),
])
def test_admin_of_a_project_refuses_a_table_it_cannot_use(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: list[str], text: str,
                                                          fault: str) -> None:
    config = tmp_path / "orchestrator.toml"
    config.write_text(text)
    monkeypatch.setattr(owlcode, "CONFIG", config)
    with pytest.raises(owlcode.Refused, match=fault):
        owlcode.argv_for(args)


ITEMS = PROJECTS.replace('[linear]\nteam = "ABC"\n', '[linear]\nteam = "ABC"\nlinear_key_env = "LINEAR_API_KEY"\n').replace(
    'paseo_port = 6768\n', 'paseo_port = 6768\npaseo_host = "100.101.102.103:6768"\npaseo_password_item = "acme-orch-agent.abc-paseo-password"\n'
).replace('paseo_port = 6769\n', 'paseo_port = 6769\nlinear_key_env = "DEF_LINEAR_API_KEY"\npaseo_password_item = "acme-orch-agent.def-paseo-password"\n')


def test_admin_of_the_orchestrator_passes_each_item_that_the_service_reads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """OWL-61: the orchestrator's own user. Its name comes from the command, and each table's linear_key_env and
    paseo_password_item go to agent_user.py, which prints a `security` command for each."""
    config = tmp_path / "orchestrator.toml"
    config.write_text(ITEMS)
    monkeypatch.setattr(owlcode, "CONFIG", config)
    assert owlcode.argv_for(["admin", "--orchestrator", "acme-orch"]) == [
        "sudo", "AGENT_USER=acme-orch", *PY, AGENT, "--", "admin", "--orchestrator",
        "--linear-env", "LINEAR_API_KEY", "--linear-env", "DEF_LINEAR_API_KEY",
        "--paseo-item", "acme-orch-agent.abc-paseo-password", "--paseo-item", "acme-orch-agent.def-paseo-password"]
    config.write_text(PROJECTS)
    assert owlcode.argv_for(["admin", "--orchestrator", "acme-orch"]) == [
        "sudo", "AGENT_USER=acme-orch", *PY, AGENT, "--", "admin", "--orchestrator"]


@pytest.mark.parametrize(("args", "fault"), [
    (["admin", "--orchestrator"], "name the orchestrator's user, and nothing else"),
    (["admin", "--orchestrator", "acme-orch", "--project", "ABC"], "name the orchestrator's user, and nothing else"),
    (["admin", "--orchestrator", "Acme Orch"], "'Acme Orch' is not a user name"),
    (["admin", "--orchestrator", "acme-xyz"], "acme-xyz is the agent_user of team XYZ: sessions run there"),
])
def test_admin_of_the_orchestrator_refuses_a_wrong_name_and_a_project_s_user(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                                             args: list[str], fault: str) -> None:
    config = tmp_path / "orchestrator.toml"
    config.write_text(PROJECTS)
    monkeypatch.setattr(owlcode, "CONFIG", config)
    with pytest.raises(owlcode.Refused, match=fault):
        owlcode.argv_for(args)
