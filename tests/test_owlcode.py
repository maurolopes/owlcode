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
