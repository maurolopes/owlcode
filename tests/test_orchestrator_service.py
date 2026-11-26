"""The orchestrator's service: the launcher that waits for the keychain, and the owner's install, with a fake for
every command (sudo, git, launchctl). Nothing here touches the system."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import plistlib
import pwd
import re
import subprocess
import sys
import tomllib
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

REAL_REPO = Path(__file__).resolve().parent.parent


def _load(name: str, path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


service = _load("orchestrator_service", REAL_REPO / "orchestrator" / "deploy" / "service.py")
installer = _load("orchestrator_install", REAL_REPO / "orchestrator" / "deploy" / "install.py")
EXAMPLE = REAL_REPO / "orchestrator" / "orchestrator.example.toml"


# -- service.py: wait for the check, then run the command in this process's place ----------------------------------------


def test_the_service_waits_one_line_per_try_then_runs_the_command() -> None:
    codes, said, slept, ran = [51, 51, 0], [], [], []
    service.wait_then_run(["security", "find"], ["orchestrator"], 60, "the keychain is locked",
                          lambda cmd: codes.pop(0), slept.append, ran.append, said.append)
    assert slept == [60, 60] and ran == [["orchestrator"]]
    assert [line.split(" ", 1)[1] for line in said] == [
        "waiting: the keychain is locked (the check exited 51); next try in 60 s",
        "waiting: the keychain is locked (the check exited 51); next try in 60 s",
        "starting the orchestrator"]


def test_the_service_needs_a_command(capsys: pytest.CaptureFixture[str]) -> None:
    assert service.main(["--check", "true"]) == 2
    assert service.main(["--check", "true", "--"]) == 2
    assert "name the command" in capsys.readouterr().err


def _pythons() -> list[str]:
    """This Python, and macOS's /usr/bin/python3 when it is 3.9: service.py runs there. On CI (Linux) the
    system Python is newer, so ruff's per-file target (pyproject.toml) holds the syntax to 3.9 there."""
    found = [sys.executable]
    system = Path("/usr/bin/python3")
    if system.exists():
        version = subprocess.run([str(system), "-c", "import sys; print(sys.version_info[:2])"], capture_output=True,
                                 text=True, timeout=60, check=False).stdout.strip()
        if version == "(3, 9)":
            found.append(str(system))
    return found


@pytest.mark.real_clock("a real process runs the check and the command")
@pytest.mark.parametrize("python", _pythons(), ids=lambda p: "system-3.9" if p == "/usr/bin/python3" else "this")
def test_the_service_runs_on_macos_python_and_execs_the_command(python: str) -> None:
    # the check prints what a keychain lookup with -w prints, the key: it must reach no output
    done = subprocess.run([python, "-I", str(REAL_REPO / "orchestrator" / "deploy" / "service.py"), "--period", "0",
                           "--check", "/bin/echo lin_api_secret", "--", sys.executable, "-c", "print('ran')"],
                          capture_output=True, text=True, timeout=60, check=False)
    assert done.returncode == 0 and done.stdout.splitlines()[-1] == "ran"
    assert "lin_api_secret" not in done.stdout + done.stderr


@pytest.mark.real_clock("a real process runs the check and the command")
@pytest.mark.parametrize("python", _pythons(), ids=lambda p: "system-3.9" if p == "/usr/bin/python3" else "this")
def test_the_service_writes_its_lines_to_the_log_file(python: str, tmp_path: Path) -> None:
    """with --log the service's lines go to the end of the file, never to launchd's output (the crash log)."""
    log = tmp_path / "orchestrator.log"
    log.write_text("an older line\n")
    done = subprocess.run([python, "-I", str(REAL_REPO / "orchestrator" / "deploy" / "service.py"), "--period", "0",
                           "--log", str(log), "--check", "true", "--", sys.executable, "-c", "print('ran')"],
                          capture_output=True, text=True, timeout=60, check=False)
    assert done.returncode == 0 and done.stdout == "ran\n"
    lines = log.read_text().splitlines()
    assert lines[0] == "an older line" and lines[1].endswith(" starting the orchestrator")


def test_the_service_log_opens_the_file_for_each_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A rotation renames the file between two lines: the second goes to the new file. A file that cannot be written gives
    the line to standard output, never a crash loop."""
    log = tmp_path / "orchestrator.log"
    write = service.append_to(str(log))
    write("one")
    log.rename(tmp_path / "orchestrator.log.0")
    write("two")
    assert (tmp_path / "orchestrator.log.0").read_text() == "one\n" and log.read_text() == "two\n"
    service.append_to(str(tmp_path / "missing" / "x.log"))("three")
    assert capsys.readouterr().out == "log not written (FileNotFoundError): three\n"


@pytest.mark.real_clock("a real shell runs the shim")
def test_the_installed_gh_shim_runs_the_agent_user_py_beside_it(tmp_path: Path) -> None:
    """`gh` found on PATH runs the agent_user.py of its own copy (the staged one in a dry round), never $HOME's."""
    copy = tmp_path / "orchestrator.new"
    (copy / "bin").mkdir(parents=True)
    (copy / "bin" / "gh").write_text(installer.GH_SHIM)
    (copy / "bin" / "gh").chmod(0o755)
    (copy / "agent_user.py").write_text("import sys\nprint(sys.argv)\n")
    done = subprocess.run(["gh", "pr", "list"], env={"PATH": f"{copy / 'bin'}:/usr/bin:/bin", "HOME": str(tmp_path)},
                          capture_output=True, text=True, timeout=60, check=False)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == str([f"{copy / 'bin'}/../agent_user.py", "gh", "pr", "list"])


# -- install.py: the owner's install ------------------------------------------------------------------------------------


HEAD, INSTALLED = "a" * 40, "b" * 40
AGENT_PLIST = "/Library/LaunchAgents/com.example.owlcode.plist"
DAEMON_PLIST = "/Library/LaunchDaemons/com.example.owlcode.plist"


class Fake:
    """The commands an install runs, each answered by what it is: never by its order."""

    def __init__(self, installed: str | None = INSTALLED, main: str = HEAD, dirty: str = "", python_ok: bool = True,
                 new_round_ok: bool = True, newsyslog_ok: bool = True, log_answer: str = "done", uid: str = "503",
                 bootstrap_ok: bool = True, preflight_ok: bool = True, known: bool = True) -> None:
        self.calls: list[list[str]] = []
        self.cwds: list[tuple[list[str], Any]] = []
        self.inputs: dict[str, Any] = {}
        self.me, self.groups = "owner", "staff admin"
        self.installed, self.main, self.dirty, self.python_ok, self.new_round_ok = installed, main, dirty, python_ok, new_round_ok
        self.newsyslog_ok, self.log_answer, self.uid, self.bootstrap_ok = newsyslog_ok, log_answer, uid, bootstrap_ok
        self.preflight_ok, self.known = preflight_ok, known

    def __call__(self, cmd: Sequence[str], stdin: Any = None, cwd: Any = None) -> subprocess.CompletedProcess[str]:
        cmd = list(cmd)
        self.calls.append(cmd)
        self.cwds.append((cmd, cwd))
        line = " ".join(cmd)

        def ok(out: str = "", code: int = 0) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(cmd, code, out, "" if code == 0 else "refused")

        if stdin is not None:
            self.inputs[cmd[-1]] = stdin
        if cmd == ["id", "-un"]:
            return ok(self.me + "\n")
        if cmd == ["id", "-Gn"]:
            return ok(self.groups + "\n")
        if cmd[0] == "git" and "rev-parse" in cmd:
            return ok((HEAD if cmd[4] == "HEAD" else self.main) + "\n")
        if "status" in cmd and "--porcelain" in cmd:
            return ok(self.dirty)
        if "cat-file" in cmd:
            return ok(code=0 if self.known else 128)
        if "log" in cmd and "--oneline" in cmd:
            assert self.known, "git log on a commit that the checkout lacks"
            return ok("1234567 Change the orchestrator (ABC-1)\n")
        if cmd[0] == "cat" and cmd[1].endswith("/COMMIT"):
            return ok(self.installed + "\n") if self.installed else ok("", 1)
        if cmd[0] == "cat" and cmd[1].endswith("/service.py"):  # an installed file that is missing
            return ok("", 1)
        if cmd[0] == "cat" and cmd[1].endswith("/model-catalog.json"):  # an installed file that equals the checkout's
            return ok((REAL_REPO / "orchestrator" / "model-catalog.json").read_text())
        if cmd[0] == "cat":  # an installed file
            return ok("# an older line\n")
        if cmd == ["id", "-u"]:  # the owner's own uid
            return ok("501\n")
        if cmd[:2] == ["id", "-u"]:
            return ok(self.uid + "\n") if self.uid else ok("", 1)
        if cmd[:3] == ["sudo", "launchctl", "bootstrap"]:
            return ok(code=0 if self.bootstrap_ok else 5)
        if "-c" in cmd and installer.PYTHON_CHECK in cmd:
            return subprocess.CompletedProcess(cmd, 0 if self.python_ok else 1, "",
                                               "" if self.python_ok else "Traceback\nssl.SSLCertVerificationError: no roots\n")
        if installer.GIVE_FILE in cmd:
            return ok(self.log_answer + "\n")
        if "newsyslog" in cmd:
            return ok(code=0 if self.newsyslog_ok else 1)
        if "--preflight" in line:
            return subprocess.CompletedProcess(cmd, int(not self.preflight_ok), "FAIL pi-chatgpt both: model not found", "")
        if "--dry-run" in line:
            new = ".new/" in line
            if new and not self.new_round_ok:
                return ok("Traceback", 1)
            return ok("slots: 0 of 2\nwould start ABC-635\n" + ("would start ABC-645\n" if new else ""))
        return ok()


def svc(tmp_path: Path) -> Any:
    config = tmp_path / "orchestrator.toml"
    config.write_text(EXAMPLE.read_text())
    return installer.load_service(config)


def ran(fake: Fake, *words: str) -> list[list[str]]:
    return [c for c in fake.calls if all(w in c for w in words)]


def test_the_install_shows_the_commits_and_both_plans_then_swaps_on_a_yes(tmp_path: Path) -> None:
    fake, said, asked = Fake(), [], []
    service_ = svc(tmp_path)
    assert installer.install(service_, REAL_REPO, fake, lambda q: asked.append(q) or "y", said.append) == 0
    text = "\n".join(said)
    assert "1234567 Change the orchestrator (ABC-1)" in text
    assert "would start ABC-635" in text and "* would start ABC-645" in text  # the new plan's extra line is marked
    assert asked and asked[0].startswith(f"Install {HEAD[:12]}")
    # the dry rounds ran as the agents' user, through each copy's own agent_user.py, before the yes
    rounds = ran(fake, "sudo", "-u", "agents")
    assert any("/usr/local/lib/orchestrator/agent_user.py" in c[-1] and "--dry-run" in c[-1] for c in rounds)
    assert any("/usr/local/lib/orchestrator.new/agent_user.py" in c[-1] for c in rounds)
    assert all("/usr/local/bin/python3 -I -u /usr/local/lib/orchestrator" in c[-1] for c in rounds if "--dry-run" in c[-1])
    order = [" ".join(c) for c in fake.calls]
    swap = order.index("sudo mv /usr/local/lib/orchestrator.new /usr/local/lib/orchestrator")
    assert order.index("sudo mv /usr/local/lib/orchestrator /usr/local/lib/orchestrator.old") < swap
    # a LaunchAgent in the agents' user domain, after the LaunchDaemon of an older install is gone
    assert ran(fake, "sudo", "launchctl", "bootstrap", "user/503", AGENT_PLIST)
    assert not ran(fake, "launchctl", "bootstrap", "system")
    boot = order.index(f"sudo launchctl bootstrap user/503 {AGENT_PLIST}")
    assert order.index("sudo launchctl bootout system/com.example.owlcode") < boot
    assert order.index(f"sudo rm -f {DAEMON_PLIST}") < boot
    assert order.index("sudo launchctl bootout user/503/com.example.owlcode") < boot
    # Set the modes before the agents' user enters the staging folder, without using the caller's umask.
    stage = str(service_.stage)
    mkdir = ["sudo", "/bin/sh", "-c", 'umask 022; exec /bin/mkdir -p "$@"', "mkdir", f"{stage}/.github", f"{stage}/bin"]
    owner = order.index(f"sudo chown -R root:wheel {stage}")
    modes = order.index(f"sudo chmod -R u=rwX,go=rX {stage}")
    shim = order.index(f"sudo chmod 755 {stage}/bin/gh")
    preflight = next(i for i, cmd in enumerate(fake.calls) if "--preflight" in cmd[-1])
    assert fake.calls.index(mkdir) < owner < modes < shim < preflight < swap
    assert fake.inputs[f"{stage}/COMMIT"] == HEAD + "\n"
    assert order.index(f"sudo tee {stage}/COMMIT") < owner
    assert not ran(fake, "tee", str(service_.install_dir / "COMMIT"))
    # its own gh shim, root's, which `agent_user.py run` puts first on PATH
    assert fake.inputs["/usr/local/lib/orchestrator.new/bin/gh"] == installer.GH_SHIM
    assert order.index("sudo tee /usr/local/lib/orchestrator.new/bin/gh") < order.index(
        "sudo chown -R root:wheel /usr/local/lib/orchestrator.new")
    assert ran(fake, "sudo", "chown", "root:wheel", AGENT_PLIST) and ran(fake, "sudo", "chmod", "644", AGENT_PLIST)
    # the local review's settings: each profile's model and effort level of the config, as JSON beside the script
    # that reads it, root's with the rest of the copy and before the service can run it
    assert json.loads(fake.inputs["/usr/local/lib/orchestrator.new/local-review.json"]) == {
        "claude": {"cells": ["claude"], "effort": "high", "model": "claude-sonnet-5-5"},
        "pi-chatgpt": {"cells": ["pi-chatgpt"], "effort": "high", "model": "gpt-6.1-sol"},
        "pi-ollama": {"cells": ["pi-ollama"], "effort": "high", "model": "deepseek-v4.1-flash"},
        "pi-lithosai": {"cells": ["pi-lithosai"], "effort": "max", "model": "zai-org/GLM-5.3-Flash"},
    }
    assert order.index("sudo tee /usr/local/lib/orchestrator.new/local-review.json") < order.index(
        "sudo chown -R root:wheel /usr/local/lib/orchestrator.new")
    # the profile in force, which `agent_user.py paseo-daemon` reads when the service starts Paseo's daemon
    assert json.loads(fake.inputs["/usr/local/lib/orchestrator.new/inference-profile.json"]) == {"inference_profile": "claude"}
    assert order.index("sudo tee /usr/local/lib/orchestrator.new/inference-profile.json") < order.index(
        "sudo chown -R root:wheel /usr/local/lib/orchestrator.new")
    # the log's rotation: root's newsyslog entry, read once by newsyslog, written before the service starts again
    conf = "/etc/newsyslog.d/com.example.owlcode.conf"
    assert fake.inputs[conf] == installer.rotation_for(service_)
    assert ran(fake, "sudo", "chown", "root:wheel", conf) and ran(fake, "sudo", "chmod", "644", conf)
    assert ran(fake, "sudo", "newsyslog", "-n", "-f", conf)
    assert order.index(f"sudo tee {conf}") < boot
    assert not any(line.startswith("WARN") for line in said)


def test_an_installed_commit_that_the_checkout_lacks_shows_the_files_diff_and_no_git_error(tmp_path: Path) -> None:
    fake, said = Fake(known=False), []
    assert installer.install(svc(tmp_path), REAL_REPO, fake, lambda q: "y", said.append) == 0
    text = "\n".join(said)
    assert (f"The installed version {INSTALLED[:12]} is not a commit of this checkout: the diff compares the installed "
            "files with this checkout's.") in text
    assert "--- installed/orchestrator.py" in text and "+++ checkout/orchestrator/orchestrator.py" in text
    assert "model-catalog.json: no change" in text  # an installed file that equals the checkout's
    assert "--- installed/service.py" in text and "-# an older line" not in text.split("--- installed/service.py")[1].split("---")[0]
    assert "-# an older line" in text and "Commits since the installed version" not in text
    assert ran(fake, "sudo", "mv", "/usr/local/lib/orchestrator.new", "/usr/local/lib/orchestrator")  # the install goes on
    assert fake.inputs[f"{svc(tmp_path).stage}/COMMIT"] == HEAD + "\n"  # the new COMMIT is this checkout's head


def test_a_domain_that_cannot_load_the_agent_says_which_command_loads_it(tmp_path: Path) -> None:
    """the user domain exists only after an SSH login of the agents' user after a restart."""
    fake = Fake(bootstrap_ok=False)
    with pytest.raises(installer.Refused, match=rf"open a pane on the machine agents, then run `sudo launchctl bootstrap user/503 {AGENT_PLIST}`"):
        installer.install(svc(tmp_path), REAL_REPO, fake, lambda q: "y", print)
    # the job is disabled in the other domains although the load failed
    assert ran(fake, "launchctl", "disable", "user/0/com.example.owlcode")


@pytest.mark.parametrize(("uid", "message"), [("", "id -u agents failed"), ("abc", "`id -u agents` gave no uid")])
def test_a_user_with_no_uid_is_refused_before_anything_changes(tmp_path: Path, uid: str, message: str) -> None:
    fake = Fake(uid=uid)
    with pytest.raises(installer.Refused, match=message):
        installer.install(svc(tmp_path), REAL_REPO, fake, lambda q: "y", print)
    assert not ran(fake, "sudo", "mkdir") and not ran(fake, "sudo", "mv") and not ran(fake, "launchctl")


def test_the_rotation_entry_rotates_both_logs_by_size_without_a_signal(tmp_path: Path) -> None:
    """each file at most LOG_ROTATE_KB before newsyslog's next run, LOG_KEEP old ones; the new file is the agents'
    user's, so the service can append to it; the flag N, since no process needs a signal."""
    lines = [line.split("\t") for line in installer.rotation_for(svc(tmp_path)).splitlines()]
    assert lines == [[path, "agents:", "640", "5", "1024", "*", "N"] for path in (
        "/Users/agents/Library/Logs/orchestrator.log", "/Users/agents/Library/Logs/orchestrator.crash.log")]


def test_the_install_gives_an_existing_log_to_the_service_user_before_the_service_loads(tmp_path: Path) -> None:
    """launchd may have created orchestrator.log as root's file; the service appends to it as the agents' user.
    One root step checks and changes the file, so no `stat` and no `chown` by path runs."""
    service_ = svc(tmp_path)
    fake = Fake()
    assert installer.install(service_, REAL_REPO, fake, lambda q: "y", print) == 0
    log = str(service_.log_file)
    give = [c for c in fake.calls if installer.GIVE_FILE in c]
    assert give == [["sudo", "/usr/bin/python3", "-I", "-c", installer.GIVE_FILE, log, service_.user]]
    assert not ran(fake, "sudo", "stat") and not ran(fake, "sudo", "chown", log)
    assert fake.calls.index(give[0]) < next(i for i, c in enumerate(fake.calls) if c[:3] == ["sudo", "launchctl", "bootstrap"])


def test_the_install_goes_on_when_there_is_no_log_yet(tmp_path: Path) -> None:
    fake, said = Fake(log_answer="missing"), []
    assert installer.install(svc(tmp_path), REAL_REPO, fake, lambda q: "y", said.append) == 0
    assert ran(fake, "sudo", "launchctl", "bootstrap") and not any(line.startswith("WARN") for line in said)


def test_the_install_warns_when_the_root_step_refuses_the_log(tmp_path: Path) -> None:
    fake, said = Fake(log_answer="refused: not a regular file with one link"), []
    assert installer.install(svc(tmp_path), REAL_REPO, fake, lambda q: "y", said.append) == 0
    assert ran(fake, "sudo", "launchctl", "bootstrap")
    assert any(line.startswith("WARN") and "owner was not changed" in line for line in said)


def _give(path: Path) -> str:
    """The root step for real, on a file of the test's own user (a chown to oneself needs no root)."""
    user = pwd.getpwuid(os.getuid()).pw_name
    done = subprocess.run([sys.executable, "-I", "-c", installer.GIVE_FILE, str(path), user],
                          capture_output=True, text=True, check=True)
    return done.stdout.strip()


def test_the_root_step_changes_a_regular_file_with_one_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOGNAME", "abc-1183-no-such-user")
    log = tmp_path / "orchestrator.log"
    log.write_text("x")
    assert _give(log) == "done"


def test_the_root_step_reports_a_missing_log(tmp_path: Path) -> None:
    assert _give(tmp_path / "none.log") == "missing"


def test_the_root_step_refuses_each_log_that_could_be_another_file(tmp_path: Path) -> None:
    """a symlink, a hard link (a second name of a file the agents' user does not own) and a directory."""
    target = tmp_path / "root-file"
    target.write_text("x")
    link, hard, folder = tmp_path / "sym.log", tmp_path / "hard.log", tmp_path / "dir.log"
    link.symlink_to(target)
    hard.hardlink_to(target)
    folder.mkdir()
    for path in (link, hard, folder):
        assert _give(path).startswith("refused"), path


def test_the_root_step_refuses_a_log_under_a_symlinked_folder(tmp_path: Path) -> None:
    """a link in a folder above the log (the agents' user owns ~/Library) must not lead the step elsewhere."""
    real = tmp_path / "rootdir"
    real.mkdir()
    (real / "orchestrator.log").write_text("x")
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "Library").symlink_to(real)
    assert _give(tmp_path / "home" / "Library" / "orchestrator.log").startswith("refused")
    assert _give(tmp_path / "home" / "Library" / "none" / "orchestrator.log").startswith("refused")


def test_the_root_step_reports_a_missing_folder_above_the_log(tmp_path: Path) -> None:
    assert _give(tmp_path / "nothing" / "orchestrator.log") == "missing"


def test_an_entry_that_newsyslog_cannot_read_warns_and_the_install_goes_on(tmp_path: Path) -> None:
    fake, said = Fake(newsyslog_ok=False), []
    assert installer.install(svc(tmp_path), REAL_REPO, fake, lambda q: "y", said.append) == 0
    assert any(line.startswith("WARN newsyslog cannot read /etc/newsyslog.d/com.example.owlcode.conf") for line in said)
    assert ran(fake, "sudo", "launchctl", "bootstrap")


def test_a_no_installs_nothing(tmp_path: Path) -> None:
    fake, said = Fake(), []
    assert installer.install(svc(tmp_path), REAL_REPO, fake, lambda q: "n", said.append) == 1
    assert said[-1] == "Nothing installed."
    assert not ran(fake, "sudo", "mv") and not ran(fake, "launchctl", "bootstrap")
    assert ran(fake, "sudo", "rm", "-rf", "/usr/local/lib/orchestrator.new")


def test_install_refuses_a_failed_staged_preflight_before_the_dry_round_or_swap(tmp_path: Path) -> None:
    fake = Fake(preflight_ok=False)
    with pytest.raises(installer.Refused, match="pi-chatgpt both: model not found"):
        installer.install(svc(tmp_path), REAL_REPO, fake, lambda _: pytest.fail("asked to install"), lambda _: None)
    [probe] = [c for c in fake.calls if "--preflight" in c[-1]]
    assert probe[:4] == ["sudo", "-u", "agents", "-H"] and ".new/orchestrator.py" in probe[-1]
    assert not ran(fake, "launchctl") and not ran(fake, "mv") and not any("--dry-run" in c[-1] for c in fake.calls)
    assert not ran(fake, "sudo", "stat")


@pytest.mark.parametrize(("step", "mode"), [("--preflight", "700"), ("--dry-run", "700"), ("--dry-run", "unavailable")])
def test_a_staged_permission_error_reports_the_folder_and_mode(tmp_path: Path, step: str, mode: str) -> None:
    service_ = svc(tmp_path)
    stage = str(service_.stage)

    class Denied(Fake):
        def __call__(self, cmd: Sequence[str], stdin: Any = None, cwd: Any = None) -> subprocess.CompletedProcess[str]:
            result = super().__call__(cmd, stdin=stdin, cwd=cwd)
            if cmd == ["sudo", "stat", "-f", "%OLp", stage]:
                if mode == "unavailable":
                    return subprocess.CompletedProcess(cmd, 1, "", "stat failed\n")
                return subprocess.CompletedProcess(cmd, 0, f"{mode}\n", "")
            if stage in cmd[-1] and step in cmd[-1]:
                return subprocess.CompletedProcess(cmd, 1, "", f"cd: {stage}: Permission denied\n")
            return result

    fake = Denied()
    with pytest.raises(installer.Refused, match=rf"Staging folder: {stage} \(mode {mode}\)"):
        installer.install(service_, REAL_REPO, fake, lambda q: "y", print)
    assert not ran(fake, "sudo", "mv")


def test_the_install_refuses_a_checkout_that_is_not_main_or_has_changes(tmp_path: Path) -> None:
    with pytest.raises(installer.Refused, match="not origin/main"):
        installer.install(svc(tmp_path), REAL_REPO, Fake(main="c" * 40), lambda q: "y", print)
    with pytest.raises(installer.Refused, match="has changes"):
        installer.install(svc(tmp_path), REAL_REPO, Fake(dirty=" M orchestrator/orchestrator.py\n"), lambda q: "y", print)


def test_the_install_refuses_a_python_without_certificates_or_a_failed_new_round(tmp_path: Path) -> None:
    with pytest.raises(installer.Refused, match=r"3\.11 or newer with root certificates"):
        installer.install(svc(tmp_path), REAL_REPO, Fake(python_ok=False), lambda q: "y", print)
    fake = Fake(new_round_ok=False)
    with pytest.raises(installer.Refused, match="dry round failed"):
        installer.install(svc(tmp_path), REAL_REPO, fake, lambda q: "y", print)
    assert not ran(fake, "launchctl", "bootstrap")


def test_a_failed_python_check_shows_its_own_error(tmp_path: Path) -> None:
    with pytest.raises(installer.Refused, match="SSLCertVerificationError: no roots"):
        installer.install(svc(tmp_path), REAL_REPO, Fake(python_ok=False), lambda q: "y", print)


def test_the_install_refuses_the_agents_user_and_a_user_that_is_not_an_admin(tmp_path: Path) -> None:
    for me, groups in (("agents", "staff admin"), ("owner", "staff")):
        fake = Fake()
        fake.me, fake.groups = me, groups
        with pytest.raises(installer.Refused, match="own admin account"):
            installer.install(svc(tmp_path), REAL_REPO, fake, lambda q: "y", print)
        assert not ran(fake, "git")


@pytest.mark.parametrize("over", [{"me": "agents"}, {"groups": "staff"}])
def test_check_refuses_the_agents_user_and_a_non_admin_before_any_sudo(tmp_path: Path, over: dict[str, Any]) -> None:
    fake = CheckFake(**over)
    with pytest.raises(installer.Refused, match="own admin account"):
        installer.check(svc(tmp_path), fake, print)
    assert not [c for c in fake.calls if c[0] == "sudo"]


def test_each_command_of_the_agents_user_starts_in_a_folder_it_can_read(tmp_path: Path) -> None:
    fake = Fake()
    installer.install(svc(tmp_path), REAL_REPO, fake, lambda q: "y", print)
    agent = [cwd for cmd, cwd in fake.cwds if cmd[:3] == ["sudo", "-u", "agents"]]
    assert len(agent) >= 3
    assert all(cwd == Path("/") for cwd in agent)
    checked = CheckFake()
    _check(tmp_path, checked)
    agent = [cwd for cmd, cwd in checked.cwds if cmd[:3] == ["sudo", "-u", "agents"]]
    assert len(agent) == 3  # the check, `which gh`, and the search for the clones of the product's user
    assert all(cwd == Path("/") for cwd in agent)


def test_a_first_install_has_no_old_copy_to_run_or_move(tmp_path: Path) -> None:
    fake, said = Fake(installed=None), []
    assert installer.install(svc(tmp_path), REAL_REPO, fake, lambda q: "y", said.append) == 0
    assert "(first install)" in "\n".join(said) and "(not installed)" in "\n".join(said)
    assert not ran(fake, "sudo", "mv", "/usr/local/lib/orchestrator", "/usr/local/lib/orchestrator.old")


def test_the_launch_agent_runs_the_installed_copy_in_the_background_session(tmp_path: Path) -> None:
    """`launchctl bootstrap user/UID` refused the plist without `LimitLoadToSessionType` Background (an Aqua
    agent, and the agents' user has no GUI session); a LaunchAgent runs as its domain's user, so no `UserName`."""
    daemon = plistlib.loads(installer.plist_for(svc(tmp_path)))
    assert daemon["LimitLoadToSessionType"] == "Background" and "UserName" not in daemon
    assert daemon["KeepAlive"] is True and daemon["RunAtLoad"] is True
    assert svc(tmp_path).plist == Path(AGENT_PLIST)
    args = daemon["ProgramArguments"]
    assert args[:3] == ["/usr/bin/python3", "-I", "/usr/local/lib/orchestrator/service.py"]
    assert args[args.index("--check") + 1].startswith("/usr/bin/security find-generic-password")
    # -w: only a lookup that reads the key fails on a locked keychain; service.py sends its output to /dev/null
    assert "-w" in args[args.index("--check") + 1].split()
    tail = args[args.index("--") + 1:]
    assert tail[:4] == ["/usr/bin/python3", "-I", "/usr/local/lib/orchestrator/agent_user.py", "run"]
    assert "/usr/local/lib/orchestrator/orchestrator.py" in tail and "--once" not in tail and "--dry-run" not in tail
    at = tail.index("/usr/local/bin/python3")  # the orchestrator's Python: isolated from the agents' site-packages
    assert tail[at:at + 4] == ["/usr/local/bin/python3", "-I", "-u", "/usr/local/lib/orchestrator/orchestrator.py"]
    assert all(not a.startswith("/Users/agents/product") for a in args)  # never the agents' checkout
    # both write the log themselves, opened again for each line; launchd's output goes to the crash log
    assert args[args.index("--user") + 1] == "agents" and args.index("--user") < args.index("--")
    assert args[args.index("--log") + 1] == "/Users/agents/Library/Logs/orchestrator.log"
    assert args.index("--log") < args.index("--")  # service.py's own option
    assert tail[-2:] == ["--log", "/Users/agents/Library/Logs/orchestrator.log"]  # orchestrator.py's
    assert daemon["StandardOutPath"] == daemon["StandardErrorPath"] == "/Users/agents/Library/Logs/orchestrator.crash.log"
    # the waiting line names the unlock that reaches the service: `owlcode unlock`
    assert args[args.index("--why") + 1] == installer.WHY.format(user="agents")
    assert "in a pane on the machine agents" in args[args.index("--why") + 1]


def test_paseo_daemon_loads_the_agent_that_the_install_writes(tmp_path: Path) -> None:
    """`agent_user.py paseo-daemon`, the owner's step after each restart, loads the service by its own label and path. The
    example's label is neutral, so a config that carries the script's label must install the plist at the path that the
    script loads."""
    agent = _load("agent_user_for_service", REAL_REPO / ".workflow" / "agent_user.py")
    config = tmp_path / "orchestrator.toml"
    config.write_text(EXAMPLE.read_text().replace('label = "com.example.owlcode"', f'label = "{agent.ORCHESTRATOR_LABEL}"'))
    assert installer.load_service(config).plist == agent.ORCHESTRATOR_PLIST


def test_a_config_without_the_service_table_is_refused(tmp_path: Path) -> None:
    config = tmp_path / "c.toml"
    config.write_text("[linear]\nteam = 'X'\n")
    with pytest.raises(installer.Refused, match=r"\[service\] needs user"):
        installer.load_service(config)
    with pytest.raises(installer.Refused, match=r"copy orchestrator/orchestrator\.example\.toml"):
        installer.load_service(tmp_path / "missing.toml")


def test_a_keychain_check_without_w_is_refused(tmp_path: Path) -> None:
    """a lookup without -w reads only the item's attributes, which a locked keychain gives too."""
    config = tmp_path / "orchestrator.toml"
    config.write_text(EXAMPLE.read_text().replace(" -w /Users/", " /Users/"))
    with pytest.raises(installer.Refused, match="keychain_check needs -w"):
        installer.load_service(config)


def test_main_prints_a_refusal_and_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert installer.main(["--config", str(tmp_path / "missing.toml")]) == 1
    assert capsys.readouterr().err.startswith("install.py: cannot read")


def test_side_by_side_marks_the_lines_that_differ() -> None:
    table = installer.side_by_side("a\nb", "a\nc", width=5)
    assert table.splitlines()[2:] == ["a     | a", "b     * c"]


PLIST = AGENT_PLIST


class CheckFake:
    """The commands `check` runs, answered by what they are. The defaults are a healthy install."""

    def __init__(self, **over: Any) -> None:
        self.over = over
        self.calls: list[list[str]] = []
        self.cwds: list[tuple[list[str], Any]] = []
        self.inputs: dict[str, Any] = {}

    def __call__(self, cmd: Sequence[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        cmd = list(cmd)
        self.calls.append(cmd)
        self.cwds.append((cmd, kw.get("cwd")))
        if kw.get("stdin") is not None:
            self.inputs[cmd[-1]] = kw["stdin"]
        line = " ".join(cmd)

        def done(out: str = "", code: int = 0, err: str = "") -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(cmd, code, out, err)

        if cmd == ["id", "-un"]:
            return done(self.over.get("me", "owner") + "\n")
        if cmd == ["id", "-Gn"]:
            return done(self.over.get("groups", "staff admin") + "\n")
        if cmd == ["id", "-u"]:  # the owner's own uid
            return self.over.get("owner_id") or done("501\n")
        if cmd[:2] == ["id", "-u"]:
            return self.over.get("id") or done("503\n")
        if cmd[:3] == ["sudo", "launchctl", "print-disabled"]:  # disabled in user/501 and user/0 by default
            shown = self.over.get("disabled", {"user/501": "disabled", "user/0": "true"}).get(cmd[3])
            body = f'\t"com.example.owlcode" => {shown}\n' if shown else ""
            return done("disabled services = {\n" + body + '\t"com.apple.other" => enabled\n}\n')
        if cmd[:3] == ["sudo", "launchctl", "print"] and cmd[3].startswith("system/"):
            return self.over.get("system_print") or done(code=113, err="Could not find service")
        if cmd[:3] == ["sudo", "launchctl", "print"] and cmd[3].startswith("user/501/"):
            return self.over.get("owner_print") or done(code=113, err="Could not find service")
        if cmd[:3] == ["sudo", "launchctl", "print"] and cmd[3].startswith("user/0/"):  # root's domain
            return self.over.get("root_print") or done(code=113, err="Could not find service")
        if cmd[:3] == ["sudo", "launchctl", "print"]:
            assert cmd[3] == "user/503/com.example.owlcode", cmd
            return self.over.get("launchctl") or done(f"{cmd[3]} = {{\n\tpath = {PLIST}\n\tstate = running\n"
                                                      "\tstdout path = /x.log\n\tpid = 4242\n}\n")
        if cmd[0] == "test":
            return done(code=0 if self.over.get("daemon_file") else 1)
        if cmd[0] == "ps":
            return done(self.over.get("ps_user", "agents") + "\n")
        if cmd[0] == "find":
            bad = self.over.get("writable_copy" if cmd[1] == "/usr/local/lib/orchestrator" else "writable_plist", "")
            return done(bad)
        if cmd[0] == "cat":
            if "plist_text" in self.over:
                return done(self.over["plist_text"])
            check = self.over.get("plist_check", "/usr/bin/security find-generic-password -w -s x")
            return done(plistlib.dumps({"Label": "x", "KeepAlive": self.over.get("keep_alive", True),
                                        "ProgramArguments": ["python", "--check", check, "--", "run"]}).decode())
        if cmd[:3] == ["dscl", ".", "-read"]:  # each product's agents' user (OWL-62)
            users = self.over.get("users", {"agents": "/Users/agents"})
            user = cmd[3].rsplit("/", 1)[-1]
            return done(f"NFSHomeDirectory: {users[user]}\n") if user in users else done(code=56, err="eDSRecordNotFound")
        if cmd[:3] == ["stat", "-f", "%Lp"]:
            return done(self.over.get("home_modes", {}).get(cmd[3], "700") + "\n")
        if "/usr/bin/find" in cmd:
            clones = self.over.get("clones", {})
            return self.over.get("find") or done("".join(f"{folder}/.git\n" for folder in clones if folder.startswith(cmd[5] + "/")))
        if "/bin/cat" in cmd:
            url = self.over.get("clones", {}).get(cmd[-1].removesuffix("/.git/config"), "")
            return done(f'[core]\n\tbare = false\n[remote "origin"]\n\turl = {url}\n' if url else "")
        if cmd[0] == "stat":
            return done(self.over.get("shim_stat", "root 755") + "\n")
        if "/usr/bin/which" in line:
            return done(self.over.get("which", "/usr/local/lib/orchestrator/bin/gh") + "\n")
        if cmd[:2] == ["sudo", "-u"] and ("find-generic-password" in cmd or "pi-signin" in cmd):  # a profile's key_check (OWL-71)
            gone = any(word in line for word in self.over.get("missing", ()))
            return done("" if gone else "fake-secret-value\n", 44 if gone else 0)
        return done("ok   keys: ABC: LINEAR_API_KEY is set\n", self.over.get("inner_code", 0))  # the installed --check


def _check(tmp_path: Path, fake: CheckFake) -> tuple[int, list[str]]:
    said: list[str] = []
    config = tmp_path / "orchestrator.toml"
    config.write_text(EXAMPLE.read_text())
    return installer.main(["check", "--config", str(config)], run=fake, out=said.append), said


def test_the_check_action_runs_the_installed_check_as_the_agents_user_and_passes_its_code(tmp_path: Path) -> None:
    fake = CheckFake(inner_code=1)
    code, said = _check(tmp_path, fake)
    assert code == 1  # the inner check failed, though every property passed
    assert said[0] == "ok   keys: ABC: LINEAR_API_KEY is set"
    cmd = next(c for c in fake.calls if c[0] == "sudo")
    assert cmd[:4] == ["sudo", "-u", "agents", "-H"]
    assert "/usr/local/lib/orchestrator/agent_user.py run -- /usr/local/bin/python3 -I -u /usr/local/lib/orchestrator/orchestrator.py" in cmd[-1]
    assert cmd[-1].endswith("--check")


def test_check_prints_each_property_of_the_install_and_passes_when_all_hold(tmp_path: Path) -> None:
    code, said = _check(tmp_path, CheckFake())
    assert code == 0
    rows = said[-1].splitlines()
    assert len(rows) == 14 and all(r.startswith("ok  ") for r in rows)
    assert rows[11:] == ["ok   ABC: the user agents exists", "ok   ABC: the home /Users/agents of agents is mode 700",
                         "ok   ABC: the user agents holds no other team's clone"], "the product's agents' user (OWL-62)"
    assert "process 4242 runs as agents" in rows[0]
    assert f"the job is loaded from {PLIST}" in rows[1]
    assert "is disabled and not loaded in the domain of the owner (user/501)" in rows[2]
    assert "is disabled and not loaded in the domain of root (user/0)" in rows[3]
    assert "no LaunchDaemon of an older install" in rows[4]
    assert "the service's PATH finds /usr/local/lib/orchestrator/bin/gh before any other gh" in rows[9]


@pytest.mark.parametrize(("over", "fragment"), [
    ({"ps_user": "root"}, "runs as root (want agents"),
    ({"ps_user": "owner"}, "runs as owner (want agents"),
    ({"launchctl": subprocess.CompletedProcess([], 0, f"path = {PLIST}\nstate = waiting\n", "")}, "is not loaded in the domain of agents or has no pid"),
    ({"launchctl": subprocess.CompletedProcess([], 0, "path = /Users/agents/Library/LaunchAgents/x.plist\npid = 4242\n", "")},
     f"the job is loaded from {PLIST} (found: /Users/agents/Library/LaunchAgents/x.plist)"),
    ({"owner_print": subprocess.CompletedProcess([], 0, f"path = {PLIST}\npid = 99\n", "")},
     "is loaded in the domain of the owner (user/501): it idles, but run `sudo launchctl bootout user/501/com.example.owlcode`"),
    ({"disabled": {"user/0": "disabled"}},
     "is not disabled in the domain of the owner (user/501): launchd loads it there after a restart; run `sudo launchctl disable user/501/com.example.owlcode`"),
    ({"disabled": {"user/501": "enabled", "user/0": "disabled"}}, "is not disabled in the domain of the owner (user/501)"),
    ({"owner_id": subprocess.CompletedProcess([], 1, "", "no uid")}, "the owner's domain: `id -u` gave no uid"),
    ({"root_print": subprocess.CompletedProcess([], 0, f"path = {PLIST}\npid = 538\n", "")},
     "is loaded in the domain of root (user/0): it idles, but run `sudo launchctl bootout user/0/com.example.owlcode`"),
    ({"disabled": {"user/501": "disabled"}},
     "is not disabled in the domain of root (user/0): launchd loads it there after a restart; run `sudo launchctl disable user/0/com.example.owlcode`"),
    ({"system_print": subprocess.CompletedProcess([], 0, "pid = 77\n", "")}, "the LaunchDaemon of an older install is loaded"),
    ({"daemon_file": True}, f"the LaunchDaemon of an older install is there (system/com.example.owlcode, {DAEMON_PLIST})"),
    ({"writable_copy": "/usr/local/lib/orchestrator/orchestrator.py\n"}, "the installed copy is root's and not writable by group or others (not so: /usr/local/lib/orchestrator/orchestrator.py)"),
    ({"writable_plist": PLIST + "\n"}, "the LaunchAgent plist is root's and not writable by group"),
    ({"keep_alive": False}, "the plist has no KeepAlive true"),
    ({"plist_text": "not a plist"}, "the plist has no KeepAlive true"),
    ({"plist_check": "/usr/bin/security find-generic-password -s x"}, "the plist's --check holds -w"),
    ({"shim_stat": "agents 755"}, "is root's, mode 755 (found: agents 755)"),
    ({"shim_stat": "root 775"}, "(found: root 775)"),
    ({"which": "/Users/agents/.local/bin/gh"}, "(found: /Users/agents/.local/bin/gh)"),
])
def test_check_fails_each_property_that_does_not_hold(tmp_path: Path, over: dict[str, Any], fragment: str) -> None:
    code, said = _check(tmp_path, CheckFake(**over))
    fails = [r for r in said[-1].splitlines() if r.startswith("FAIL")]
    # an unreadable plist also leaves the --check line with nothing to read
    assert code == 1 and len(fails) == (2 if "plist_text" in over else 1) and any(fragment in f for f in fails), fails


@pytest.mark.parametrize("over", [{"launchctl": subprocess.CompletedProcess([], 113, "", "not found")},
                                  {"id": subprocess.CompletedProcess([], 1, "", "no such user")}])
def test_check_fails_a_service_that_is_not_in_the_agents_user_domain(tmp_path: Path, over: dict[str, Any]) -> None:
    """no job of the label in user/UID (or no uid) fails the process line and the plist line."""
    code, said = _check(tmp_path, CheckFake(**over))
    fails = [r for r in said[-1].splitlines() if r.startswith("FAIL")]
    assert code == 1 and len(fails) == 2
    assert "is not loaded in the domain of agents" in fails[0] and "(found: nothing)" in fails[1]


def test_check_reports_a_command_that_fails_as_a_line_not_an_exception(tmp_path: Path) -> None:
    def run(cmd: Sequence[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        if cmd[0] == "id":
            return subprocess.CompletedProcess(cmd, 0, "owner" if cmd[1] == "-un" else "staff admin", "")
        return subprocess.CompletedProcess(cmd, 1, "", "no such file")

    code, said = _check(tmp_path, run)  # type: ignore[arg-type]
    # all but the line on an older LaunchDaemon (none is found), and the product's user, which `dscl` does not find
    assert code == 1 and said[-1].count("FAIL") == 11 and said[-1].endswith("FAIL ABC: no user agents: run `owlcode admin --project ABC`")


class _Stop(Exception):
    pass


def test_the_service_as_another_user_writes_one_line_and_idles_without_the_check() -> None:
    """loaded into the owner's domain, the job must not run the check or the command, and must not exit (KeepAlive)."""
    said: list[str] = []
    slept: list[float] = []

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        if len(slept) == 3:
            raise _Stop

    with pytest.raises(_Stop):
        service.idle_as_other_user("agents", "owner", said.append, sleep)
    assert len(said) == 1 and "runs only as agents, not as owner" in said[0] and slept == [3600] * 3


@pytest.mark.real_clock("a real process runs service.py")
def test_the_service_main_refuses_a_user_that_is_not_the_named_one(tmp_path: Path) -> None:
    log = tmp_path / "s.log"
    proc = subprocess.Popen([sys.executable, "-I", str(REAL_REPO / "orchestrator" / "deploy" / "service.py"), "--user", "no-such-user-abc1065",
                             "--log", str(log), "--check", "true", "--", "touch", str(tmp_path / "ran")])
    try:
        # Wait for the property, not a time: the refusal line appears while the process stays alive (pytest-timeout ends a hang).
        while not (log.exists() and "runs only as no-such-user-abc1065" in log.read_text()):
            assert proc.poll() is None, "the service exited before it wrote the refusal"
            time.sleep(0.05)
        assert proc.poll() is None and not (tmp_path / "ran").exists()  # it idles: no exit, and the command never ran
    finally:
        proc.kill()
        proc.wait()


@pytest.mark.real_clock("a real process runs service.py")
def test_the_service_main_runs_the_command_as_the_named_user(tmp_path: Path) -> None:
    """with `--user` equal to the current user the service goes on to the check and the command."""
    me = pwd.getpwuid(os.getuid()).pw_name
    done = subprocess.run([sys.executable, "-I", str(REAL_REPO / "orchestrator" / "deploy" / "service.py"), "--period", "0", "--user", me,
                           "--check", "true", "--", sys.executable, "-c", "print('ran')"],
                          capture_output=True, text=True, timeout=60, check=False)
    assert done.returncode == 0 and "ran" in done.stdout and "refused" not in done.stdout


# -- install.py: the keys that the installed config lacks ----------------------------------------------------------


def _without(tmp_path: Path, *keys: str) -> Any:
    """A service whose config is the example minus the lines that start with each key."""
    config = tmp_path / "orchestrator.toml"
    kept = [line for line in EXAMPLE.read_text().splitlines() if not any(line.startswith(f"{k} =") for k in keys)]
    config.write_text("\n".join(kept) + "\n")
    return installer.load_service(config)


def test_install_refuses_invalid_config_before_staging(tmp_path: Path) -> None:
    line, error = "bad = [", "cannot read the installed config as TOML"
    service_, fake = _without(tmp_path), Fake()
    original = line + "\n" + service_.config.read_text()
    service_.config.write_text(original)
    with pytest.raises(installer.Refused, match=error):
        installer.install(service_, REAL_REPO, fake, lambda _: pytest.fail("unexpected confirmation"), print)
    assert not ran(fake, "sudo") and service_.config.read_text() == original


def test_a_missing_key_is_listed_and_added_after_a_yes_in_its_table(tmp_path: Path) -> None:
    service_, fake, said, asked = _without(tmp_path, "clone", "queue_issue"), Fake(), [], []
    assert installer.install(service_, REAL_REPO, fake, lambda q: asked.append(q) or "y", said.append) == 0
    text = "\n".join(said)
    assert "the config's changes" in text and "(new in [service])" in text and "(new in the top of the file)" in text
    assert len(asked) == 1 and asked[0].startswith(f"Install {HEAD[:12]}, add 2 keys in {service_.config}")
    written = fake.inputs[str(service_.config)]
    import tomllib

    assert tomllib.loads(written) == tomllib.loads(EXAMPLE.read_text())
    assert written.index("queue_issue") < written.index("[run_settings]")  # the top-level key stays above the tables
    assert written.index("clone =") > written.index("[service]")
    assert "keychain_check" in written and "# The check that the service waits on" in written  # the other lines stay


def test_the_installed_code_runs_on_the_config_as_it_was_and_the_new_code_on_a_staged_copy(tmp_path: Path) -> None:
    """the installed code calls a new key unknown, so its round reads the installed file; root's file changes after the yes."""
    service_, fake = _without(tmp_path, "clone"), Fake()
    assert installer.install(service_, REAL_REPO, fake, lambda q: "y", print) == 0
    staged = "/usr/local/lib/orchestrator.new/orchestrator.toml"
    rounds = [c[-1] for c in ran(fake, "sudo", "-u", "agents") if "--dry-run" in c[-1]]
    old, new = (next(r for r in rounds if ".new/" not in r), next(r for r in rounds if ".new/" in r))
    assert f"--config {service_.config} " in old and f"--config {staged} " in new
    assert "clone =" in fake.inputs[staged] and "clone =" in fake.inputs[str(service_.config)]
    order = [" ".join(c) for c in fake.calls]
    assert order.index(f"sudo tee {staged}") < order.index(f"sudo tee {service_.config}")
    assert order.index(f"sudo tee {staged}") < order.index("sudo chown -R root:wheel /usr/local/lib/orchestrator.new")
    assert max(i for i, c in enumerate(order) if "--dry-run" in c) < order.index(f"sudo tee {service_.config}")


def test_a_no_to_the_install_leaves_the_config_as_it_was(tmp_path: Path) -> None:
    service_, fake, said = _without(tmp_path, "clone"), Fake(), []
    before = service_.config.read_text()
    assert installer.install(service_, REAL_REPO, fake, lambda q: "n", said.append) == 1
    assert service_.config.read_text() == before and str(service_.config) not in fake.inputs
    assert not ran(fake, "sudo", "mv") and str(service_.config) not in [c[-1] for c in ran(fake, "sudo", "tee")]


def test_a_whole_missing_table_is_added_at_the_end(tmp_path: Path) -> None:
    config = tmp_path / "orchestrator.toml"
    text = EXAMPLE.read_text()
    config.write_text(text[: text.index("[github]")] + text[text.index("# The service:"):])
    service_, fake = installer.load_service(config), Fake()
    assert installer.install(service_, REAL_REPO, fake, lambda q: "y", print) == 0
    import tomllib

    assert tomllib.loads(fake.inputs[str(config)]) == tomllib.loads(text)


def test_a_value_that_the_installed_config_has_is_never_changed_only_printed(tmp_path: Path) -> None:
    service_, fake, said, asked = svc(tmp_path), Fake(), [], []
    service_.config.write_text(service_.config.read_text().replace("max_runs = 3", "max_runs = 5"))
    assert installer.install(service_, REAL_REPO, fake, lambda q: asked.append(q) or "y", said.append) == 0
    assert "max_runs: installed 5, example 3" in "\n".join(said)
    assert str(service_.config) not in fake.inputs and "max_runs = 5" in service_.config.read_text()
    assert len(asked) == 1  # only the install's own question: no key was missing


def test_keys_that_cannot_be_added_safely_are_refused_and_nothing_is_written(tmp_path: Path) -> None:
    """An example value on many lines would give root's config invalid TOML. The refusal names the
    example file and the keys, never the cut first line of a value."""
    service_, fake = _without(tmp_path, "poll_seconds"), Fake()
    checkout = tmp_path / "checkout"
    (checkout / "orchestrator").mkdir(parents=True)
    (checkout / "orchestrator" / "orchestrator.example.toml").write_text(
        EXAMPLE.read_text().replace("poll_seconds = 60", "poll_seconds = [\n    60,\n]"))
    before = service_.config.read_text()
    with pytest.raises(installer.Refused, match=r"orchestrator\.example\.toml has it: poll_seconds") as refused:
        installer.plan_config(service_, checkout, print)
    assert "poll_seconds = [" not in str(refused.value)
    assert service_.config.read_text() == before and not fake.calls


class RestartFake(CheckFake):
    """CheckFake that also answers the dry round, `kickstart` and the read of the installed local review's settings, by what
    each command is (`review_before`: its text, or a code that says it is not there)."""

    def __call__(self, cmd: Sequence[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        line = " ".join(cmd)
        if list(cmd)[:2] == ["cat", "/usr/local/lib/orchestrator/inference-profile.json"]:  # not there before
            self.calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 1, "", "")
        if list(cmd)[:2] == ["cat", "/usr/local/lib/orchestrator/local-review.json"]:
            self.calls.append(list(cmd))
            before = self.over.get("review_before")
            return subprocess.CompletedProcess(cmd, 0 if isinstance(before, str) else 1, before or "", "")
        if "--preflight" in line:
            self.calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, self.over.get("preflight_code", 0), "FAIL pi-chatgpt both: model not found", "")
        if "--dry-run" in line:
            self.calls.append(list(cmd))
            code = self.over.get("dry_code", 0)
            return subprocess.CompletedProcess(cmd, code, self.over.get("dry_out", "slots: 0 of 3\n") if code == 0 else "", "" if code == 0 else "unknown key: typo")
        if list(cmd)[:3] == ["sudo", "launchctl", "kickstart"]:
            self.calls.append(list(cmd))
            code = self.over.get("kickstart_code", 0)
            return subprocess.CompletedProcess(cmd, code, "", "" if code == 0 else "Could not find service")
        return super().__call__(cmd, **kw)


def _restart(tmp_path: Path, fake: RestartFake) -> tuple[int, list[str], str]:
    said: list[str] = []
    config = tmp_path / "orchestrator.toml"
    config.write_text(EXAMPLE.read_text())
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = installer.main(["restart", "--config", str(config)], run=fake, out=said.append)
    return code, said, err.getvalue()


def test_restart_runs_a_dry_round_then_the_uid_then_kickstart_then_the_check(tmp_path: Path) -> None:
    fake = RestartFake()
    code, said, _ = _restart(tmp_path, fake)
    assert code == 0
    at = {name: next(i for i, c in enumerate(fake.calls) if pred(c)) for name, pred in {
        "preflight": lambda c: "--preflight" in c[-1],
        "dry": lambda c: "--dry-run" in " ".join(c),
        "uid": lambda c: c[:2] == ["id", "-u"],
        "kick": lambda c: c[:3] == ["sudo", "launchctl", "kickstart"],
        "check": lambda c: "--check" in c[-1] and c[0] == "sudo",
        "print": lambda c: c[:3] == ["sudo", "launchctl", "print"],
    }.items()}
    assert at["preflight"] < at["dry"] < at["uid"] < at["kick"] < at["check"] < at["print"]
    dry = fake.calls[at["dry"]]
    assert dry[:4] == ["sudo", "-u", "agents", "-H"] and "/usr/local/lib/orchestrator/orchestrator.py" in dry[-1]
    assert fake.calls[at["kick"]] == ["sudo", "launchctl", "kickstart", "-k", "user/503/com.example.owlcode"]
    assert any("orchestrator.crash.log" in s for s in said) and said[-1].count("ok  ") == 14
    # the local review's settings follow an edit of the config, written before the dry round, so the copy that the
    # dry round runs reads them; a session that starts after this restart gets the model that the config names now
    assert json.loads(fake.inputs["/usr/local/lib/orchestrator/local-review.json"])["claude"] == {
        "cells": ["claude"], "effort": "high", "model": "claude-sonnet-5-5"}
    target = "/usr/local/lib/orchestrator/local-review.json"
    write = fake.calls.index(["sudo", "tee", target])
    owner = fake.calls.index(["sudo", "chown", "root:wheel", target])
    mode = fake.calls.index(["sudo", "chmod", "644", target])
    assert write < owner < mode < at["dry"]
    # and the profile in force, so the Paseo daemon that the restarted service starts holds the config's profile
    profile = "/usr/local/lib/orchestrator/inference-profile.json"
    assert json.loads(fake.inputs[profile]) == {"inference_profile": "claude"}
    assert fake.calls.index(["sudo", "chmod", "644", profile]) < at["dry"]


def test_the_profile_in_force_needs_its_table(tmp_path: Path) -> None:
    text = EXAMPLE.read_text().replace('inference_profile = "claude"', 'inference_profile = "nobody"', 1)
    with pytest.raises(installer.Refused, match=r"inference_profile 'nobody' has no \[profiles.NAME\] table"):
        installer.profile_setting(text)


def test_a_profile_without_its_local_review_stops_the_install_before_anything_changes(tmp_path: Path) -> None:
    """the local review's model and effort level are each profile's keys of the config, and nothing defaults
    them. A config that lacks one stops the install before it writes anything, and names the key as the example
    has it."""
    text = EXAMPLE.read_text()
    assert json.loads(installer.review_settings(text))["pi-ollama"] == {"cells": ["pi-ollama"], "effort": "high",
                                                                        "model": "deepseek-v4.1-flash"}
    for missing, said in (
        (re.sub(r"(?m)^review_effort = .*$", "", text, count=1), r"\[profiles\.(claude|ollama|pi-ollama|pi-chatgpt)\] review_effort "
                                                               r"is missing"),
        (text.replace('review_model = "claude-sonnet-5-5"', 'review_model = "a model"'), "review_model is missing or is no name"),
        (re.sub(r"(?ms)^\[profiles\..*?(?=^\[)", "", text), r"names no \[profiles.NAME\] table"),
    ):
        with pytest.raises(installer.Refused, match=said):
            installer.review_settings(missing)
    # a key that the installed config lacks is added from the example first, so only a value of its own that is no
    # name reaches this refusal: nothing is written before it, and nothing is swapped
    fake, said = Fake(), []
    service_ = svc(tmp_path)
    service_.config.write_text(text.replace('review_model = "claude-sonnet-5-5"', 'review_model = "claude sonnet 5.5"'))
    with pytest.raises(installer.Refused, match="review_model"):
        installer.install(service_, REAL_REPO, fake, lambda q: "y", said.append)
    assert not [c for c in fake.calls if c[:2] == ["sudo", "tee"]], "nothing is written before the refusal"
    assert not [c for c in fake.calls if "mv" in c], "and nothing is swapped"


def test_restart_never_names_the_system_domain(tmp_path: Path) -> None:
    fake = RestartFake()
    _restart(tmp_path, fake)
    kick = next(c for c in fake.calls if c[:3] == ["sudo", "launchctl", "kickstart"])
    assert not any(a.startswith("system/") for a in kick)


def test_restart_refuses_a_failed_preflight_before_the_dry_round_or_kickstart(tmp_path: Path) -> None:
    fake = RestartFake(preflight_code=1, review_before="old settings")
    code, _, err = _restart(tmp_path, fake)
    assert code == 1 and "pi-chatgpt both: model not found" in err
    assert all("--dry-run" not in c[-1] and "kickstart" not in c for c in fake.calls)
    assert "/usr/local/lib/orchestrator/local-review.json" not in fake.inputs


def test_restart_leaves_the_service_alone_when_the_dry_round_fails(tmp_path: Path) -> None:
    """Its local review's settings too: the service keeps the config it loaded, so
    the run line it writes names the review model of that config, and a file that a failed round left in place would have
    its sessions review on another one."""
    fake = RestartFake(dry_code=1)
    code, _, err = _restart(tmp_path, fake)
    assert code == 1 and "unknown key: typo" in err and "not restarted" in err
    assert not [c for c in fake.calls if "kickstart" in c]
    assert ["sudo", "rm", "-f", "/usr/local/lib/orchestrator/local-review.json"] in fake.calls, "no file before: none after"
    assert ["sudo", "rm", "-f", "/usr/local/lib/orchestrator/inference-profile.json"] in fake.calls, "the profile in force too"


def test_restart_puts_the_local_review_settings_back_when_the_dry_round_fails(tmp_path: Path) -> None:
    target = "/usr/local/lib/orchestrator/local-review.json"
    fake = RestartFake(dry_code=1, review_before='{"claude": {"model": "glm-5.3", "effort": "medium"}}\n')
    code, _, err = _restart(tmp_path, fake)
    assert code == 1 and "were not changed" in err
    assert fake.inputs[target].startswith('{"claude": {"model": "glm-5.3"'), "the bytes of before, put back"
    wrote = [c for c in fake.calls if c == ["sudo", "tee", target]]
    assert len(wrote) == 2, "the new file first, then the old one back"


def test_restart_prints_the_bootstrap_command_when_the_job_is_not_loaded(tmp_path: Path) -> None:
    fake = RestartFake(kickstart_code=113)
    code, _, err = _restart(tmp_path, fake)
    assert code == 1
    assert f"sudo launchctl bootstrap user/503 {AGENT_PLIST}" in err and "SSH login" in err
    assert not [c for c in fake.calls if "--check" in c[-1]]  # no check after a restart that failed


@pytest.mark.parametrize("over", [{"me": "agents"}, {"groups": "staff"}])
def test_restart_refuses_the_agents_user_and_a_non_admin_before_any_sudo(tmp_path: Path, over: dict[str, Any]) -> None:
    fake = RestartFake(**over)
    code, _, err = _restart(tmp_path, fake)
    assert code == 1 and "own admin account" in err
    assert not [c for c in fake.calls if c[0] == "sudo"]


def _unlock(tmp_path: Path, fake: CheckFake, tty_code: int = 0, check: str | None = None) -> tuple[int, list[str], str, list[list[str]]]:
    said: list[str] = []
    typed: list[list[str]] = []
    config = tmp_path / "orchestrator.toml"
    text = EXAMPLE.read_text()
    if check is not None:
        text = re.sub(r'(?m)^keychain_check = .*$', f'keychain_check = "{check}"', text)
    config.write_text(text)

    def tty(cmd: Sequence[str]) -> int:
        typed.append(list(cmd))
        return tty_code

    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = installer.main(["unlock", "--config", str(config)], run=fake, out=said.append, tty=tty)
    return code, said, err.getvalue(), typed


def test_unlock_runs_security_in_the_agents_domain_session_on_the_terminal(tmp_path: Path) -> None:
    """after a restart a pane's unlock stays in the pane's session; `launchctl asuser UID` joins the domain's."""
    code, said, _, typed = _unlock(tmp_path, CheckFake())
    assert code == 0
    assert typed == [["sudo", "launchctl", "asuser", "503", "sudo", "-u", "agents", "/usr/bin/security",
                      "unlock-keychain", "/Users/agents/Library/Keychains/agent.keychain-db"]]
    assert "owlcode check" in said[-1]


def test_unlock_says_the_domain_needs_an_ssh_login_when_security_fails(tmp_path: Path) -> None:
    code, _, err, typed = _unlock(tmp_path, CheckFake(), tty_code=1)
    assert code == 1 and len(typed) == 1
    assert "user/503" in err and "SSH login" in err


def test_unlock_refuses_a_check_that_names_no_keychain_file(tmp_path: Path) -> None:
    code, _, err, typed = _unlock(tmp_path, CheckFake(), check="/usr/bin/security find-generic-password -s x -w")
    assert code == 1 and "names no keychain file" in err and typed == []


@pytest.mark.parametrize("over", [{"me": "agents"}, {"groups": "staff"}])
def test_unlock_refuses_the_agents_user_and_a_non_admin_before_any_sudo(tmp_path: Path, over: dict[str, Any]) -> None:
    code, _, err, typed = _unlock(tmp_path, CheckFake(**over))
    assert code == 1 and "own admin account" in err and typed == []


def test_the_waiting_line_names_the_unlock_task() -> None:
    assert "owlcode unlock" in installer.WHY and "does not reach" in installer.WHY


def test_the_install_disables_the_job_in_the_owners_and_roots_domains_after_the_load(tmp_path: Path) -> None:
    """launchd loads the plist into the owner's domain at login and root's at boot; disabled, it stays out."""
    fake = Fake()
    assert installer.install(svc(tmp_path), REAL_REPO, fake, lambda q: "y", [].append) == 0
    order = [" ".join(c) for c in fake.calls]
    boot = order.index(f"sudo launchctl bootstrap user/503 {AGENT_PLIST}")
    for uid in ("501", "0"):
        assert order.index(f"sudo launchctl disable user/{uid}/com.example.owlcode") > boot
        assert f"sudo launchctl bootout user/{uid}/com.example.owlcode" in order
    assert "sudo launchctl disable user/503/com.example.owlcode" not in order


def test_a_key_added_before_a_product_table_lands_in_the_table_before_it_not_in_the_product() -> None:
    """`[[product]]` ends the table above it, so the install's added key stays out of the product's table."""
    installed = '[linear]\nteam = "ABC"\n\n[[product]]\nteam = "XYZ"\nrepo = "a/b"\nrun_cwd = "."\n'
    example = '[linear]\nteam = "ABC"\nread_states = ["Todo"]\n'
    text = installer.add_keys(installed, installer.missing_keys(example, installed))
    assert text.index("read_states") < text.index("[[product]]")
    assert tomllib.loads(text)["linear"]["read_states"] == ["Todo"] and "read_states" not in tomllib.loads(text)["product"][0]


def test_the_install_adds_the_profile_tables_once_and_reads_a_dotted_table_as_a_table_in_a_table() -> None:
    """`[profiles.claude]` is the table `claude` inside `profiles`; read as one name, its keys would count as missing
    at every install and be added twice."""
    example = 'inference_profile = "claude"\n\n[profiles.claude]\nneither = ["a", "b"]\n\n[profiles.ollama]\nneither = ["c", "d"]\n'
    installed = 'poll_seconds = 60\n'
    text = installer.add_keys(installed, installer.missing_keys(example, installed))
    assert tomllib.loads(text) == {"poll_seconds": 60, "inference_profile": "claude",
                                   "profiles": {"claude": {"neither": ["a", "b"]}, "ollama": {"neither": ["c", "d"]}}}
    assert installer.missing_keys(example, text) == {} and installer.different_values(example, text) == []
    assert installer.different_values(example, text.replace('["c", "d"]', '["e", "f"]')) == [
        "  [profiles.ollama] neither: installed ['e', 'f'], example ['c', 'd']"]


def test_the_install_adds_each_profile_s_permission_mode_to_a_config_made_before_it_and_keeps_every_other_value() -> None:
    """the installed config has no `permission_mode`, and the orchestrator refuses a profile without one: the install
    adds the example's line to each profile table, in its table, and changes no value the owner set."""
    example = EXAMPLE.read_text()
    installed = re.sub(r"(?m)^permission_mode = .*\n", "", example).replace('both = ["claude-opus-5-5", "high"]', 'both = ["owner-model", "high"]')
    assert not any("permission_mode" in table for table in tomllib.loads(installed)["profiles"].values())
    missing = installer.missing_keys(example, installed)
    assert set(missing) == {"profiles.claude"} and set(missing["profiles.claude"]) == {"permission_mode"}
    text = installer.add_keys(installed, missing)
    profiles = tomllib.loads(text)["profiles"]
    assert profiles["claude"]["permission_mode"] == "auto"
    assert profiles["claude"]["both"] == ["owner-model", "high"]
    assert installer.missing_keys(example, text) == {}


def test_the_install_writes_the_profiles_that_each_profiles_cells_run_on(tmp_path: Path) -> None:
    """`agent_user.py paseo-daemon --profile NAME` reads no TOML (Python 3.9), so the settings file names, for each
    profile, itself first and then each other profile that a cell names (`[model, effort, PROFILE]`, or `[model, PROFILE]`),
    in the order of the cells and once."""
    text = EXAMPLE.read_text()
    mixed = re.sub(r'(?m)^(intricate|both) = \["deepseek-ai/DeepSeek-V4.1-Flash", "high"\]$',
                   r'\1 = ["claude-opus-5-5", "high", "claude"]', text)
    mixed = re.sub(r'(?m)^judgment = \["deepseek-ai/DeepSeek-V4.1-Flash", "high"\]$', 'judgment = ["glm-5.3", "pi-ollama"]', mixed)
    found = json.loads(installer.review_settings(mixed))
    assert found["pi-lithosai"]["cells"] == ["pi-lithosai", "claude", "pi-ollama"]
    assert found["claude"]["cells"] == ["claude"], "only one level: the named profile's own cells play no part"
    nameless = re.sub(r'(?m)^intricate = \["claude-opus-5-5", "high", "claude"\]$', 'intricate = ["claude-opus-5-5", "high", "nothing"]', mixed)
    with pytest.raises(installer.Refused, match=r"\[profiles.pi-lithosai\] intricate names 'nothing', which has no"):
        installer.review_settings(nameless)


# -- `set`, `pause` and `resume` --------------------------------------------------------------------------------


def _set(tmp_path: Path, fake: RestartFake, *items: str, action: str = "set") -> tuple[int, list[str], str, Path]:
    said: list[str] = []
    config = tmp_path / "orchestrator.toml"
    config.write_text(EXAMPLE.read_text())
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = installer.main([action, *items, "--config", str(config)], run=fake, out=said.append)
    return code, said, err.getvalue(), config


def test_set_changes_a_top_level_key_and_keeps_the_comments_and_the_order() -> None:
    text = EXAMPLE.read_text()
    new = installer.set_keys(text, {"inference_profile": "pi-lithosai", "max_runs": "5", "dry_run": "false"})
    assert tomllib.loads(new)["inference_profile"] == "pi-lithosai" and tomllib.loads(new)["max_runs"] == 5
    assert tomllib.loads(new)["dry_run"] is False
    assert [line for line in new.splitlines() if line.startswith("inference_profile")] == ['inference_profile = "pi-lithosai"']
    # the comment of a changed line stays, and so does everything else
    assert "# how many runs may build at once" in new and "# true: print each decision" in new
    diff = installer.config_diff(text, new)
    assert diff[0].startswith("-max_runs = 3 ") and diff[1].startswith("+max_runs = 5 ")
    assert diff[0][1:].replace("3", "5", 1) == diff[1][1:], "only the value of the line changes"
    assert len(installer.config_diff(text, new)) == 6
    assert tomllib.loads(text) | {"inference_profile": "pi-lithosai", "max_runs": 5, "dry_run": False} == tomllib.loads(new)


@pytest.mark.parametrize("item, said", [
    ("neither=[\"x\", \"high\"]", r"not a top-level key.*Keys that take it: .*inference_profile"),  # a key of a table
    ("typo=1", r"not a top-level key"),
    ("max_runs=many", r"max_runs takes a int"),
    ("inference_profile", r"is not KEY=VALUE"),
])
def test_set_refuses_a_table_key_an_unknown_key_a_wrong_type_and_a_bare_word(item: str, said: str) -> None:
    with pytest.raises(installer.Refused, match=said):
        installer.set_keys(EXAMPLE.read_text(), installer.parse_assignments([item]))


def test_set_prints_the_diff_checks_a_copy_then_writes_and_restarts(tmp_path: Path) -> None:
    fake = RestartFake()
    code, said, _, config = _set(tmp_path, fake, "inference_profile=pi-lithosai")
    assert code == 0
    assert said[:4] == [f"{config}:", '-inference_profile = "claude"', '+inference_profile = "pi-lithosai"', "preflight: ok"]
    assert said[4] == "dry round: ok (0 would start, 0 would wake)"
    counted = RestartFake(dry_out="would start ABC-1 (m, medium)\nwould start ABC-2 (m, medium)\nwould start ABC-3 (m, medium)\n"
                                "would wake the session of ABC-4 (x)\n")
    assert "dry round: ok (3 would start, 1 would wake)" in _set(tmp_path, counted, "max_runs=4")[1]
    staged = f"{config}.new"
    at = {name: next(i for i, c in enumerate(fake.calls) if pred(c)) for name, pred in {
        "stage": lambda c: c == ["sudo", "tee", staged],
        "preflight": lambda c: "--preflight" in c[-1] and staged in c[-1],
        "dry": lambda c: "--dry-run" in c[-1] and staged in c[-1],
        "move": lambda c: c[:2] == ["sudo", "mv"] and c[2:] == [staged, str(config)],
        "kick": lambda c: c[:3] == ["sudo", "launchctl", "kickstart"]}.items()}
    assert at["stage"] < at["preflight"] < at["dry"] < at["move"] < at["kick"]
    assert 'inference_profile = "pi-lithosai"' in fake.inputs[staged]
    assert "the service restarted" in said[-1]


def test_a_failed_check_of_set_writes_nothing_and_leaves_the_service(tmp_path: Path) -> None:
    for over in ({"preflight_code": 1}, {"dry_code": 1}):
        fake = RestartFake(**over)
        code, _said, err, config = _set(tmp_path, fake, "inference_profile=pi-lithosai")
        assert code == 1 and "nothing was written" in err
        assert 'inference_profile = "claude"' in config.read_text()
        assert not [c for c in fake.calls if c[:2] == ["sudo", "mv"] or "kickstart" in c]
        assert ["sudo", "rm", "-f", f"{config}.new"] in fake.calls, "the staged copy goes"


def test_set_with_a_refused_key_calls_nothing(tmp_path: Path) -> None:
    fake = RestartFake()
    code, _, err, _ = _set(tmp_path, fake, "typo=1")
    assert code == 1 and "Keys that take it" in err and str(tmp_path / "orchestrator.toml") in err
    assert not [c for c in fake.calls if c[0] == "sudo"]


def test_pause_writes_the_file_the_service_reads_and_resume_removes_it(tmp_path: Path) -> None:
    fake = RestartFake()
    code, said, _, _config = _set(tmp_path, fake, action="pause")
    flag = str(tmp_path / "orchestrator.paused")
    assert code == 0 and said == ["paused: no new starts until `owlcode resume`"]
    assert ["sudo", "tee", flag] in fake.calls and ["sudo", "chown", "root:wheel", flag] in fake.calls
    assert not [c for c in fake.calls if "kickstart" in c], "the service stays up: it reads the file in its next round"
    fake = RestartFake()
    code, said, _, _ = _set(tmp_path, fake, action="resume")
    assert code == 0 and ["sudo", "rm", "-f", flag] in fake.calls and said[0].startswith("resumed")


def test_set_with_no_words_the_same_value_and_pause_with_words(tmp_path: Path) -> None:
    fake = RestartFake()
    code, _, err, _ = _set(tmp_path, fake)
    assert code == 1 and "name a key" in err and not [c for c in fake.calls if c[0] == "sudo"]
    code, said, _, _ = _set(tmp_path, fake, "max_runs=3")
    assert code == 0 and said[-1].startswith("no change") and not [c for c in fake.calls if c[0] == "sudo"]
    code, _, err, _ = _set(tmp_path, fake, "extra", action="pause")
    assert code == 1 and "pause takes no arguments" in err and not [c for c in fake.calls if c[0] == "sudo"]


# -- install.py: the profile menu ---------------------------------------------------------------------------------------


class ProfileFake(RestartFake):
    """RestartFake that also answers the installed catalog and each profile's `key_check` (`missing`: the profiles whose
    credential is not there)."""

    def __call__(self, cmd: Sequence[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        if list(cmd)[:2] == ["cat", "/usr/local/lib/orchestrator/model-catalog.json"]:
            self.calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0, (REAL_REPO / "orchestrator" / "model-catalog.json").read_text(), "")
        if list(cmd)[:4] == ["sudo", "-u", "agents", "-H"] and ("find-generic-password" in cmd or "pi-signin" in " ".join(cmd)):  # a key_check
            self.calls.append(list(cmd))
            self.cwds.append((list(cmd), kw.get("cwd")))
            absent = any(word in " ".join(cmd) for word in self.over.get("missing", ()))
            return subprocess.CompletedProcess(cmd, int(absent), "", "")
        return super().__call__(cmd, **kw)


def _profile(tmp_path: Path, fake: ProfileFake, *items: str, answers: Sequence[str] = ("",)) -> tuple[int, list[str], str, Path, list[str]]:
    said: list[str] = []
    asked: list[str] = []
    replies = iter(answers)

    def ask(question: str) -> str:
        asked.append(question)
        return next(replies)

    config = tmp_path / "orchestrator.toml"
    config.write_text(EXAMPLE.read_text())
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = installer.main(["profile", *items, "--config", str(config)], run=fake, ask=ask, out=said.append)
    return code, said, err.getvalue(), config, asked


def test_the_profile_list_shows_each_profile_with_its_cells_prices_credential_and_the_one_in_force(tmp_path: Path) -> None:
    fake = ProfileFake(missing=("lithosai-key",))
    code, said, _, config, asked = _profile(tmp_path, fake)
    text = "\n".join(said)
    assert code == 0 and said[-1] == "no change" and len(asked) == 1 and "(1-4)" in asked[0]
    tables = tomllib.loads(EXAMPLE.read_text())["profiles"]
    assert [line.split()[1] for line in said if re.match(r" \d\. ", line)] == list(tables)
    assert " 1. claude  (in force)" in said and " 2. pi-ollama" in said, "only the profile in force is marked"
    assert text.count("(in force)") == 1
    block = text.split(" 1. claude")[1].split(" 2. pi-ollama")[0]
    assert "harness claude, provider anthropic, credential ok" in block
    assert re.search(r"neither +claude-sonnet-5-5 +medium +subscription", block)
    assert re.search(r"both +claude-opus-5-5 +high +subscription", block)
    assert re.search(r"review +claude-sonnet-5-5 +high +subscription", block)
    lithosai = text.split(" 4. pi-lithosai")[1]
    assert "provider lithosai, credential MISSING" in lithosai
    assert re.search(r"neither +deepseek-ai/DeepSeek-V4.1-Flash +high +\$0.15/\$0.003/\$1.2$", lithosai.splitlines()[2])
    assert re.search(r"review +zai-org/GLM-5.3-Flash +max +\$0.15/\$0.03/\$0.5$", next(line for line in lithosai.splitlines() if line.strip().startswith("review")))
    assert "credential ok" in text.split(" 3. pi-chatgpt")[1].split(" 4. ")[0]
    checks = [c for c in fake.calls if c[:4] == ["sudo", "-u", "agents", "-H"] and c[4] != "/bin/sh"]
    assert len(checks) == len(tables) and all(cwd == Path("/") for c, cwd in fake.cwds if c in checks)
    assert config.read_text() == EXAMPLE.read_text() and not [c for c in fake.calls if c[:2] == ["sudo", "tee"]]


def test_a_cell_on_another_profile_names_it_and_prices_the_models_of_that_profile() -> None:
    text = EXAMPLE.read_text().replace('intricate = ["glm-5.3", "high"]', 'intricate = ["claude-opus-5-5", "high", "claude"]', 1)
    catalog = json.loads((REAL_REPO / "orchestrator" / "model-catalog.json").read_text())
    _, lines = installer.profile_menu(text, catalog, lambda _: True)
    row = next(line for line in lines if "claude-opus-5-5" in line and "on claude" in line)
    assert re.search(r"intricate +claude-opus-5-5 +high +subscription on claude$", row)


def test_every_cell_form_is_read_and_a_bad_cell_or_config_is_refused() -> None:
    catalog = json.loads((REAL_REPO / "orchestrator" / "model-catalog.json").read_text())
    tables = {"claude": {}, "pi-x": {}}
    assert installer.cell_parts("pi-x", {"both": ["m", "high"]}, tables, "both") == ("m", "high", "pi-x")
    assert installer.cell_parts("pi-x", {"both": ["m", "claude"]}, tables, "both") == ("m", "", "claude")
    assert installer.cell_parts("pi-x", {"both": ["m"]}, tables, "both") == ("m", "", "pi-x")
    assert installer.cell_parts("pi-x", {"both": ["m", "low", "claude"]}, tables, "both") == ("m", "low", "claude")
    for bad, said in ((["m", "low", "nope"], "has no [profiles.nope] table"), ("m", "is not a list"), ([], "is not a list"),
                      (["a", "b", "claude", "d"], "is not a list")):
        with pytest.raises(installer.Refused, match=re.escape(said)):
            installer.cell_parts("pi-x", {"both": bad}, tables, "both")
    assert installer.price_text(catalog, "anthropic", "no-such-model") == "not in the catalog"
    assert installer.price_text(catalog, "nobody", "claude-sonnet-5-5") == "not in the catalog"
    text = EXAMPLE.read_text().replace('both = ["glm-5.3", "high"]', 'both = ["glm-5.3"]', 1)
    assert any(re.search(r"both +glm-5.3 +- +\$1.4/\$0.26/\$4.4$", line) for line in installer.profile_menu(text, catalog, lambda _: True)[1])
    for config, said in (("this is = not toml [", "does not parse"), ('inference_profile = "x"\n', "names no [profiles.NAME] table"),
                         ('[profiles]\nx = 1\n', "is not a table")):
        with pytest.raises(installer.Refused, match=re.escape(said)):
            installer.profile_menu(config, catalog, lambda _: True)


def test_picking_a_number_changes_only_the_inference_profile_after_the_checks(tmp_path: Path) -> None:
    fake = ProfileFake()
    code, said, _, config, _ = _profile(tmp_path, fake, answers=("4",))
    assert code == 0 and "the service restarted" in said[-1]
    assert [line for line in said if line.startswith(("+", "-"))] == ['-inference_profile = "claude"', '+inference_profile = "pi-lithosai"']
    staged = f"{config}.new"
    assert installer.config_diff(EXAMPLE.read_text(), fake.inputs[staged]) == ['-inference_profile = "claude"', '+inference_profile = "pi-lithosai"'], \
        "the edit changes one line and keeps the file's other text and comments"
    at = {name: next(i for i, c in enumerate(fake.calls) if pred(c)) for name, pred in {
        "preflight": lambda c: "--preflight" in c[-1] and staged in c[-1],
        "move": lambda c: c[:2] == ["sudo", "mv"] and c[2:] == [staged, str(config)],
        "kick": lambda c: c[:3] == ["sudo", "launchctl", "kickstart"]}.items()}
    assert at["preflight"] < at["move"] < at["kick"]


def test_a_name_switches_with_no_list_and_no_question(tmp_path: Path) -> None:
    fake = ProfileFake()
    code, said, _, config, asked = _profile(tmp_path, fake, "pi-chatgpt", answers=())
    assert code == 0 and not asked and "+inference_profile = \"pi-chatgpt\"" in said
    assert not [line for line in said if line.startswith(" 1. ")], "no list"
    assert 'inference_profile = "pi-chatgpt"' in fake.inputs[f"{config}.new"]


def test_a_failed_preflight_of_the_picked_profile_leaves_the_old_value_and_the_service(tmp_path: Path) -> None:
    for over in ({"preflight_code": 1}, {"dry_code": 1}):
        fake = ProfileFake(**over)
        code, _, err, config, _ = _profile(tmp_path, fake, answers=("3",))
        assert code == 1 and "nothing was written" in err
        assert config.read_text() == EXAMPLE.read_text(), "the old value stays in the file"
        assert not [c for c in fake.calls if c[:2] == ["sudo", "mv"] or "kickstart" in c]


@pytest.mark.parametrize("answer", ["0", "5", "x", "-1", "1.5"])
def test_a_wrong_number_changes_nothing(tmp_path: Path, answer: str) -> None:
    fake = ProfileFake()
    code, _, err, config, _ = _profile(tmp_path, fake, answers=(answer,))
    assert code == 1 and "is not a number from 1 to 4" in err and config.read_text() == EXAMPLE.read_text()
    assert not [c for c in fake.calls if c[:2] == ["sudo", "tee"]]


def test_an_unknown_name_lists_the_valid_ones_and_two_names_are_refused(tmp_path: Path) -> None:
    fake = ProfileFake()
    code, _, err, _, _ = _profile(tmp_path, fake, "nope")
    assert code == 1 and "'nope' is not a profile" in err
    assert "claude, pi-ollama, pi-chatgpt, pi-lithosai" in err
    assert not [c for c in fake.calls if c[0] == "sudo"]
    code, _, err, _, _ = _profile(tmp_path, fake, "claude", "pi-ollama")
    assert code == 1 and "name one profile" in err


def test_the_profile_list_with_no_one_to_answer_is_refused(tmp_path: Path) -> None:
    def no_input(_: str) -> str:
        raise EOFError

    config = tmp_path / "orchestrator.toml"
    config.write_text(EXAMPLE.read_text())
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = installer.main(["profile", "--config", str(config)], run=ProfileFake(), ask=no_input, out=lambda _: None)
    assert code == 1 and "no answer" in err.getvalue()


# -- install.py: the retired keys ---------------------------------------------------------------------------------


def _with_retired(tmp_path: Path, old: str, after: str = "# The service:") -> Any:
    """A service whose config is the example with `old` (lines) put before `after`."""
    service_ = svc(tmp_path)
    text = service_.config.read_text()
    service_.config.write_text(text.replace(after, old + after, 1))
    return service_


def test_a_retired_key_is_shown_with_the_new_keys_and_removed_on_a_yes(tmp_path: Path) -> None:
    import tomllib

    service_ = _without(tmp_path, "queue_issue")
    text = service_.config.read_text()
    service_.config.write_text('run_command = "old {key}"\n' + text)
    fake, said, asked = Fake(), [], []
    assert installer.install(service_, REAL_REPO, fake, lambda q: asked.append(q) or "y", said.append) == 0
    shown = "\n".join(said)
    assert '-run_command = "old {key}"' in shown and "(retired: the harness and the host build the start command)" in shown and "(new in the top of the file)" in shown
    assert asked[0].startswith(f"Install {HEAD[:12]}, change 1 retired key and add 1 key in {service_.config}")
    written = fake.inputs[str(service_.config)]
    assert "\nrun_command" not in "\n" + written and tomllib.loads(written) == tomllib.loads(EXAMPLE.read_text())
    assert "# The check that the service waits on" in written  # a comment of another line stays
    staged = "/usr/local/lib/orchestrator.new/orchestrator.toml"  # the new code's dry round reads the config as it will be
    assert "\nrun_command" not in "\n" + fake.inputs[staged]


def test_a_retired_key_alone_is_removed_with_no_new_key(tmp_path: Path) -> None:
    service_ = _with_retired(tmp_path, '[github_old]\nx = 1\n\n')  # a table the retired list does not name stays
    assert installer.plan_config(service_, REAL_REPO, lambda _: None)[1] == ""
    text = service_.config.read_text().replace("conflict_comment =", 'conflict_marker = "old"\nconflict_comment =', 1)
    service_.config.write_text(text)
    new, summary = installer.plan_config(service_, REAL_REPO, lambda _: None)
    assert summary == "change 1 retired key" and "conflict_marker" not in new and "conflict_comment =" in new


def test_a_retired_table_goes_with_its_lines_and_its_neighbours_stay(tmp_path: Path) -> None:
    import tomllib

    service_ = svc(tmp_path)
    text = service_.config.read_text()
    old = '[profiles.ollama]\nkey_check = "x"\ncustom = 1\n\n'
    service_.config.write_text(text.replace("[profiles.pi-ollama]", old + "[profiles.pi-ollama]", 1))
    new, summary = installer.plan_config(service_, REAL_REPO, lambda _: None)
    assert summary == "change 1 retired key" and "[profiles.ollama]" not in new
    assert tomllib.loads(new) == tomllib.loads(text) and "\n\n\n" not in new  # the neighbours stay, one blank line between tables


def test_a_retired_profile_name_is_renamed_and_the_host_set(tmp_path: Path) -> None:
    service_ = svc(tmp_path)
    text = service_.config.read_text().replace('inference_profile = "claude"', 'inference_profile = "ollama"   # my note', 1)
    assert 'session_host = "herdr"' in text
    service_.config.write_text(text)
    said: list[str] = []
    new, summary = installer.plan_config(service_, REAL_REPO, said.append)
    assert 'inference_profile = "pi-ollama"   # my note' in new and 'session_host = "paseo"' in new
    assert summary == "change 1 retired key" and '+session_host = "paseo"' in said[-1]


def test_a_retired_value_on_many_lines_is_refused_and_nothing_is_written(tmp_path: Path) -> None:
    service_ = svc(tmp_path)
    original = 'run_command = """\nold\n"""\n' + service_.config.read_text()
    service_.config.write_text(original)
    fake = Fake()
    with pytest.raises(installer.Refused, match=r"cannot remove the retired keys safely.*: run_command \(the harness and the host build the start command\)"):
        installer.install(service_, REAL_REPO, fake, lambda _: pytest.fail("unexpected confirmation"), print)
    assert service_.config.read_text() == original and not ran(fake, "sudo")


def test_a_no_leaves_a_config_with_a_retired_key_as_it_was(tmp_path: Path) -> None:
    service_ = svc(tmp_path)
    original = 'run_command = "old"\n' + service_.config.read_text()
    service_.config.write_text(original)
    fake = Fake()
    assert installer.install(service_, REAL_REPO, fake, lambda q: "n", print) == 1
    assert service_.config.read_text() == original and str(service_.config) not in fake.inputs


def test_a_retired_table_keeps_the_comment_above_the_next_table(tmp_path: Path) -> None:
    service_ = svc(tmp_path)
    text = service_.config.read_text()
    old = '[profiles.ollama]\nkey_check = "x"\n\n# about the next table\n'
    service_.config.write_text(text.replace("[profiles.pi-ollama]", old + "[profiles.pi-ollama]", 1))
    new, _ = installer.plan_config(service_, REAL_REPO, lambda _: None)
    assert "# about the next table\n[profiles.pi-ollama]" in new and "[profiles.ollama]" not in new and 'key_check = "x"' not in new


def test_the_retired_cells_of_run_settings_go_and_its_other_keys_stay(tmp_path: Path) -> None:
    import tomllib

    service_ = svc(tmp_path)
    text = service_.config.read_text()
    service_.config.write_text(text.replace("[run_settings]\n", '[run_settings]\nneither = ["m", "low"]\n    # about it\nboth = ["m", "high"]\n', 1))
    said: list[str] = []
    new, summary = installer.plan_config(service_, REAL_REPO, said.append)
    assert summary == "change 2 retired keys" and "about it" not in new and "neither =" not in new.split("[profiles.claude]")[0]
    assert tomllib.loads(new) == tomllib.loads(text)
    assert 'neither = ["m", "low"]' in said[-1] and "(retired: the cells moved to the [profiles.NAME] tables)" in said[-1]


# -- each team's Linear key: the service gives the process a key besides LINEAR_API_KEY ----------------------------

DEF = '\n[[product]]\nteam = "DEF"\nrepo = "owner/def"\nrun_cwd = "~/def"\nlinear_key_env = "DEF_LINEAR_API_KEY"\n'
LOOKUP = ("/usr/bin/security find-generic-password -a agents -s agents-agent.def-linear-key -w "
          "/Users/agents/Library/Keychains/agent.keychain-db")


def svc_with(tmp_path: Path, extra: str = DEF) -> Any:
    config = tmp_path / "orchestrator.toml"
    config.write_text(EXAMPLE.read_text() + extra)
    return installer.load_service(config)


def test_the_service_puts_the_output_of_each_lookup_in_the_environment_and_prints_no_value() -> None:
    said: list[str] = []
    replies = {"owl": (0, "owl-secret"), "gone": (44, ""), "empty": (0, "")}
    env = {"GONE_KEY": "left by the caller", "KEEP": "1"}
    service.env_from(["ABC_KEY=owl", "GONE_KEY=gone", "EMPTY_KEY=empty"], lambda cmd: replies[cmd[0]], env, said.append)
    assert env == {"ABC_KEY": "owl-secret", "KEEP": "1"}  # a value from the caller never stands in for a key that is not there
    assert [line.split(" ", 1)[1] for line in said] == ["no key for GONE_KEY: its lookup exited 44",
                                                         "no key for EMPTY_KEY: its lookup printed nothing"]
    assert "owl-secret" not in "\n".join(said) and "left by the caller" not in "\n".join(said)


@pytest.mark.parametrize("spec", ["ABC_KEY", "owl_key=x", "=x", "ABC_KEY=", "A B=x", "1A=x"])
def test_the_service_refuses_a_wrong_env_from_option(spec: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert service.main(["--env-from", spec, "--", "true"]) == 2
    assert "--env-from takes VAR=LOOKUP" in capsys.readouterr().err


@pytest.mark.real_clock("a real process runs the lookup and the command")
@pytest.mark.parametrize("python", _pythons(), ids=lambda p: "system-3.9" if p == "/usr/bin/python3" else "this")
def test_the_service_runs_the_command_with_the_key_and_waits_for_nothing_without_a_check(python: str, tmp_path: Path) -> None:
    """Both layers of the plist's command, as the service runs them: with no `--check` it starts the command at once, and the
    lookup's output reaches the command's environment, the log and the standard output never see it."""
    log = tmp_path / "orchestrator.log"
    env = {"PATH": "/usr/bin:/bin", "DEF_LINEAR_API_KEY": "from the caller"}
    done = subprocess.run([python, "-I", str(REAL_REPO / "orchestrator" / "deploy" / "service.py"), "--log", str(log),
                           "--env-from", "DEF_LINEAR_API_KEY=/bin/echo owl-secret", "--env-from", "OTHER_API_KEY=/usr/bin/false",
                           "--", sys.executable, "-c", "import os; print(os.environ.get('DEF_LINEAR_API_KEY'), os.environ.get('OTHER_API_KEY'))"],
                          capture_output=True, text=True, timeout=60, check=False, env=env)
    assert done.returncode == 0 and done.stdout == "owl-secret None\n"
    text = log.read_text()
    assert text.splitlines()[-1].endswith("no key for OTHER_API_KEY: its lookup exited 1")  # no "starting" line: the outer step wrote it
    assert "starting the orchestrator" not in done.stdout
    assert "owl-secret" not in text + done.stderr


def test_a_team_in_a_second_workspace_has_its_key_item_named_by_its_variable(tmp_path: Path) -> None:
    assert installer.key_item("LINEAR_API_KEY") == "linear-key" and installer.key_item("DEF_LINEAR_API_KEY") == "def-linear-key"
    agent = _load("agent_user_for_keys", REAL_REPO / ".workflow" / "agent_user.py")
    assert installer.key_item("LINEAR_API_KEY") in {name for name, *_ in agent.KEYS}, "the item that `run` reads for LINEAR_API_KEY"
    assert svc(tmp_path).keys == ()  # the example names no second key: the service is as it was
    assert svc_with(tmp_path).keys == ("DEF_LINEAR_API_KEY",)
    assert svc_with(tmp_path, DEF + DEF.replace("DEF", "OW2").replace("owner/def", "owner/ow2").replace("~/def", "~/ow2")).keys == ("DEF_LINEAR_API_KEY", "OW2_LINEAR_API_KEY")
    two = DEF.replace('team = "DEF"', 'team = "TWO"').replace("owner/def", "owner/two").replace("~/def", "~/two")  # two teams of one workspace: one lookup
    assert svc_with(tmp_path, DEF + two).keys == ("DEF_LINEAR_API_KEY",)
    assert svc_with(tmp_path, DEF.replace("DEF_LINEAR_API_KEY", "LINEAR_API_KEY")).keys == ()
    with pytest.raises(installer.Refused, match="team DEF: linear_key_env must be the name of an environment variable"):
        svc_with(tmp_path, DEF.replace("DEF_LINEAR_API_KEY", "owl; key"))


def test_the_lookup_of_a_key_is_the_config_s_keychain_check_with_the_item_of_that_key(tmp_path: Path) -> None:
    service_ = svc_with(tmp_path)
    assert installer.key_lookup(service_, "DEF_LINEAR_API_KEY") == LOOKUP.split()
    assert "-w" in installer.key_lookup(service_, "DEF_LINEAR_API_KEY")
    config = tmp_path / "bad.toml"
    config.write_text((EXAMPLE.read_text() + DEF).replace("/usr/bin/security find-generic-password", "/usr/bin/true"))
    with pytest.raises(installer.Refused, match="keychain_check must be a `security find-generic-password"):
        installer.key_lookup(installer.load_service(config), "DEF_LINEAR_API_KEY")


def test_the_launch_agent_runs_the_orchestrator_through_a_second_service_step_for_a_second_key(tmp_path: Path) -> None:
    """`agent_user.py run` clears the key variables of its caller, so the lookup runs inside it, as the plist's last layer."""
    args = plistlib.loads(installer.plist_for(svc_with(tmp_path)))["ProgramArguments"]
    run = args.index("run")
    inner = args[run + 2:]  # after `agent_user.py run --`
    assert inner[:3] == ["/usr/bin/python3", "-I", "/usr/local/lib/orchestrator/service.py"]
    assert "--check" not in inner and "--user" not in inner  # nothing to wait for: the outer step waited
    assert inner[inner.index("--env-from") + 1] == f"DEF_LINEAR_API_KEY={LOOKUP}"
    assert inner[inner.index("--log") + 1] == "/Users/agents/Library/Logs/orchestrator.log"
    tail = inner[inner.index("--") + 1:]
    assert tail[:4] == ["/usr/local/bin/python3", "-I", "-u", "/usr/local/lib/orchestrator/orchestrator.py"] and tail[-2:] == inner[inner.index("--log"):inner.index("--log") + 2]
    assert args[:args.index("--")].count("--env-from") == 0, "the outer step holds the wait only"
    assert "owl-secret" not in " ".join(args)
    plain = plistlib.loads(installer.plist_for(svc(tmp_path)))["ProgramArguments"]
    assert "--env-from" not in plain and plain[plain.index("run") + 1] == "--"  # a config with no second key: the plist as it was


def test_each_command_of_the_installed_copy_gets_the_keys_but_the_offline_preflight(tmp_path: Path) -> None:
    service_ = svc_with(tmp_path)
    check = installer.as_agent(service_, service_.install_dir, "--check")[-1]
    assert "agent_user.py run -- /usr/bin/python3 -I /usr/local/lib/orchestrator/service.py --env-from " in check
    assert "DEF_LINEAR_API_KEY=" in check and check.index("service.py") < check.index("orchestrator.py") and check.endswith("--check")
    assert "service.py" not in installer.as_agent(service_, service_.install_dir, "--preflight")[-1]
    plain = installer.as_agent(svc(tmp_path), svc(tmp_path).install_dir, "--check")[-1]
    assert "service.py" not in plain  # no second key: the command as it was
    staged = installer.as_agent(service_, service_.stage, "--once", "--dry-run")[-1]
    assert "/usr/local/lib/orchestrator.new/service.py --env-from" in staged  # the dry round of a new copy reads each team too


def test_check_reports_the_plist_s_lookup_of_each_second_key(tmp_path: Path) -> None:
    service_ = svc_with(tmp_path)
    config = service_.config
    said: list[str] = []
    healthy = plistlib.dumps({"Label": "x", "KeepAlive": True, "ProgramArguments": plistlib.loads(installer.plist_for(service_))["ProgramArguments"]}).decode()
    code = installer.main(["check", "--config", str(config)], run=CheckFake(plist_text=healthy), out=said.append)
    rows = said[-1].splitlines()
    key = next(r for r in rows if "DEF_LINEAR_API_KEY" in r)
    assert code == 0 and len(rows) == 18 and key.startswith("ok  ") and "the plist gives the service DEF_LINEAR_API_KEY" in key
    assert "(the keychain item def-linear-key)" in key
    old = plistlib.dumps({"Label": "x", "KeepAlive": True, "ProgramArguments": ["python", "--check", "security find-generic-password -w", "--", "run"]}).decode()
    said.clear()
    code = installer.main(["check", "--config", str(config)], run=CheckFake(plist_text=old), out=said.append)
    last = next(r for r in said[-1].splitlines() if "DEF_LINEAR_API_KEY" in r)
    assert code == 1 and last.startswith("FAIL") and "DEF_LINEAR_API_KEY" in last
    assert "run `owlcode install`" in last and "a restart keeps the plist" in last


def test_the_dropped_opencode_tables_go_and_the_config_loads_again(tmp_path: Path) -> None:
    """an install of an earlier version holds [profiles.opencode-ollama] and [harnesses.opencode]. The new code refuses the
    profile (it is not in the catalog), so the install removes both tables, and the config then loads."""
    service_ = svc(tmp_path)
    text = service_.config.read_text()
    old = '[profiles.opencode-ollama]\nharness = "opencode"\nkey_check = "x"\n\n[harnesses.opencode]\nskill_prompt = "/implement {key}"\n\n'
    service_.config.write_text(text.replace("[profiles.pi-ollama]", old + "[profiles.pi-ollama]", 1))
    new, summary = installer.plan_config(service_, REAL_REPO, lambda _: None)
    assert summary == "change 2 retired keys"
    assert "opencode" not in new and tomllib.loads(new) == tomllib.loads(text) and "\n\n\n" not in new
    service_.config.write_text(new)
    orch = _load("orchestrator_for_abc_1326", REAL_REPO / "orchestrator" / "orchestrator.py")
    assert orch.load_config(service_.config).inference_profile == "claude"


# -- each product's agents' user (OWL-62, design b2) ---------------------------------------------------------------------

TWO_USERS = '\n[[product]]\nteam = "DEF"\nrepo = "owner/def"\nrun_cwd = "~/def"\nagent_user = "acme-def"\npaseo_port = 6769\n'


def two_users(tmp_path: Path, extra: str = TWO_USERS, top: str = 'agent_user = "acme-abc"\npaseo_port = 6768\n') -> Any:
    """The example's first product (team ABC, acme/product) in the user acme-abc, and the product DEF in acme-def."""
    config = tmp_path / "orchestrator.toml"
    config.write_text(top + EXAMPLE.read_text() + extra)
    return installer.load_service(config)


def test_the_config_names_each_product_s_user_and_a_product_with_none_has_the_service_s(tmp_path: Path) -> None:
    assert two_users(tmp_path).projects == (installer.ProjectUser("ABC", "acme/product", "acme-abc"),
                                            installer.ProjectUser("DEF", "owner/def", "acme-def"))
    assert svc_with(tmp_path).projects == (installer.ProjectUser("ABC", "acme/product", "agents"),
                                           installer.ProjectUser("DEF", "owner/def", "agents")), "the setup of before"


@pytest.mark.parametrize(("top", "extra", "fault"), [
    ('agent_user = "Acme"\n', "", "the agent_user of team ABC must be a user name"),
    ('paseo_port = 80\n', "", "the paseo_port of team ABC must be a whole number from 1024 to 65535"),
    ('paseo_port = "6768"\n', "", "the paseo_port of team ABC must be a whole number"),
    ('agent_user = "acme-abc"\npaseo_port = 6768\n', TWO_USERS.replace("6769", "6768"),
     "team DEF gives the user acme-def the paseo_port 6768 of the user acme-abc"),
])
def test_the_config_refuses_a_wrong_user_or_port_and_one_port_for_two_users(tmp_path: Path, top: str, extra: str, fault: str) -> None:
    with pytest.raises(installer.Refused, match=re.escape(fault)):
        two_users(tmp_path, extra, top)


def test_one_user_may_name_its_port_in_two_products(tmp_path: Path) -> None:
    same = TWO_USERS.replace("acme-def", "acme-abc").replace("6769", "6768")
    assert {p.user for p in two_users(tmp_path, same).projects} == {"acme-abc"}


def test_check_has_three_lines_for_each_product_s_user(tmp_path: Path) -> None:
    fake = CheckFake(users={"acme-abc": "/Users/acme-abc", "acme-def": "/Users/acme-def"},
                     home_modes={"/Users/acme-def": "750"},
                     clones={"/Users/acme-abc/product": "https://github.com/acme/product.git",
                             "/Users/acme-abc/def": "git@github.com:owner/def.git",
                             "/Users/acme-def/def": "https://github.com/owner/def"})
    rows = installer.project_lines(two_users(tmp_path), fake)
    assert [line for _, line in rows] == [
        "ok   ABC: the user acme-abc exists",
        "ok   ABC: the home /Users/acme-abc of acme-abc is mode 700",
        "FAIL ABC: the user acme-abc holds a clone of DEF (/Users/acme-abc/def)",
        "ok   DEF: the user acme-def exists",
        "FAIL DEF: the home /Users/acme-def of acme-def is mode 750, not 700: run `owlcode admin --project DEF`",
        "ok   DEF: the user acme-def holds no other team's clone",
    ]
    find = next(c for c in fake.calls if "/usr/bin/find" in c and "/Users/acme-abc" in c)
    assert find[:5] == ["sudo", "-u", "acme-abc", "-H", "/usr/bin/find"], "as the user: root follows none of its links"
    assert "/Users/acme-abc/Library" in find and "-prune" in find, "the folders where macOS's privacy control refuses a read"
    assert ["sudo", "-u", "acme-abc", "-H", "/bin/cat", "--", "/Users/acme-abc/def/.git/config"] in fake.calls, "git runs nothing"
    assert all(cwd == Path("/") for cmd, cwd in fake.cwds if cmd[:2] == ["sudo", "-u"])


def test_check_names_a_missing_user_and_a_search_that_fails(tmp_path: Path) -> None:
    fake = CheckFake(users={"acme-abc": "/Users/acme-abc"},
                     find=subprocess.CompletedProcess([], 1, "", "find: /Users/acme-abc/x: Permission denied\n"))
    rows = installer.project_lines(two_users(tmp_path), fake)
    assert [line for _, line in rows] == [
        "ok   ABC: the user acme-abc exists",
        "ok   ABC: the home /Users/acme-abc of acme-abc is mode 700",
        "FAIL ABC: cannot list the clones of acme-abc (find: /Users/acme-abc/x: Permission denied)",
        "FAIL DEF: no user acme-def: run `owlcode admin --project DEF`",
    ]


# -- each profile's key check as each product's user (OWL-71, design b2) ----------------------------------------------

def test_check_runs_each_profile_s_key_check_as_each_product_s_user_and_prints_no_value(tmp_path: Path) -> None:
    service_ = two_users(tmp_path)
    text = service_.config.read_text()
    service_.config.write_text(text.replace('both = ["claude-opus-5-5", "high"]', 'both = ["gpt-6.1-sol", "high", "pi-chatgpt"]', 1))
    fake = CheckFake(users={"acme-abc": "/Users/acme-abc", "acme-def": "/Users/acme-def"},
                     missing=("acme-def-agent.claude-token", "/Users/acme-abc/agent_user.py"))
    rows = installer.key_lines(service_, fake)
    assert [line for _, line in rows] == [
        "ok   ABC: claude's key is in acme-abc's keychain",
        "FAIL ABC: pi-chatgpt's sign-in is not there for acme-abc: run, as acme-abc, `pi`, then `/login`",
        "FAIL DEF: claude's key is not in acme-def's keychain: run, as acme-def, `/usr/bin/python3 -I ~/agent_user.py user`",
        "ok   DEF: pi-chatgpt's sign-in is there for acme-def",
    ]
    assert ["sudo", "-u", "acme-abc", "-H", "/usr/bin/security", "find-generic-password", "-a", "acme-abc", "-s",
            "acme-abc-agent.claude-token", "/Users/acme-abc/Library/Keychains/agent.keychain-db"] in fake.calls
    assert ["sudo", "-u", "acme-def", "-H", "/usr/bin/python3", "-I", "/Users/acme-def/agent_user.py", "pi-signin", "--provider",
            "openai-codex"] in fake.calls
    assert all(cwd == Path("/") for cmd, cwd in fake.cwds if cmd[:2] == ["sudo", "-u"])
    assert not any("fake-secret-value" in line for _, line in rows)


def test_check_runs_no_key_check_for_a_product_in_the_service_s_user_or_with_no_user(tmp_path: Path) -> None:
    fake = CheckFake(users={"agents": "/Users/agents"})
    assert installer.key_lines(svc_with(tmp_path), fake) == [], "the installed --check finds the service's own keys"
    fake = CheckFake(users={"acme-abc": "/Users/acme-abc"})
    rows = installer.key_lines(two_users(tmp_path), fake)
    assert [line for _, line in rows] == ["ok   ABC: claude's key is in acme-abc's keychain"], "project_lines names the missing acme-def"


def test_the_whole_check_prints_the_key_lines_after_the_users_and_fails_on_one(tmp_path: Path) -> None:
    service_ = two_users(tmp_path)
    said: list[str] = []
    fake = CheckFake(users={"acme-abc": "/Users/acme-abc", "acme-def": "/Users/acme-def"}, missing=("acme-def-agent.claude-token",))
    assert installer.main(["check", "--config", str(service_.config)], run=fake, out=said.append) == 1
    lines = said[-1].splitlines()
    assert lines[-2:] == ["ok   ABC: claude's key is in acme-abc's keychain",
                          "FAIL DEF: claude's key is not in acme-def's keychain: run, as acme-def, `/usr/bin/python3 -I ~/agent_user.py user`"]
    assert "fake-secret-value" not in "\n".join(said)


# -- the orchestrator's own user (OWL-61, design b2) -----------------------------------------------------------------------

ORCH_HOME = "/Users/acme-orch"
ENDPOINTS = TWO_USERS.replace("paseo_port = 6769\n", 'paseo_port = 6769\npaseo_host = "100.101.102.103:6769"\n')


def orchestrator_svc(tmp_path: Path, extra: str = ENDPOINTS, host: str = 'paseo_host = "100.101.102.103:6768"\n',
                     session_host: str = "paseo") -> Any:
    """Two products, each in its own user with its own session endpoint, and the service in the user acme-orch."""
    text = EXAMPLE.read_text().replace('session_host = "herdr"', f'session_host = "{session_host}"', 1)
    text = text.replace('user = "agents"  ', 'user = "acme-orch"', 1).replace('clone = "/Users/agents/owlcode"', 'clone = "~/owlcode"', 1)
    config = tmp_path / "orchestrator.toml"
    config.write_text('agent_user = "acme-abc"\npaseo_port = 6768\n' + host + text + extra)
    return installer.load_service(config)


class OrchFake(CheckFake):
    """CheckFake, with the home of acme-orch: `names` the users' full names (acme-orch is `acme-orch orchestrator`, as
    `admin --orchestrator` names it), `found` what the search prints, `processes` the user's processes, `sshd` whether
    sshd's file of the user is there."""

    def __call__(self, cmd: Sequence[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        cmd = list(cmd)
        if cmd[:3] == ["dscl", ".", "-read"] and cmd[-1] == "RealName":
            self.calls.append(cmd)
            names = self.over.get("names", {"acme-orch": "acme-orch orchestrator"})
            user = cmd[3].rsplit("/", 1)[-1]
            return (subprocess.CompletedProcess(cmd, 0, f"RealName:\n {names[user]}\n", "") if user in names
                    else subprocess.CompletedProcess(cmd, 56, "", "eDSRecordNotFound"))
        if cmd[:3] == ["sudo", "-u", "acme-orch"] and "/usr/bin/find" in cmd:
            self.calls.append(cmd)
            self.cwds.append((cmd, kw.get("cwd")))
            return self.over.get("search") or subprocess.CompletedProcess(cmd, 0, self.over.get("found", ""), "")
        if cmd[:2] == ["ps", "-U"]:
            self.calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, self.over.get("processes", ""), "")
        if cmd[:2] == ["test", "-e"]:
            self.calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0 if self.over.get("sshd") else 1, "", "")
        return super().__call__(cmd, **kw)


def test_the_orchestrator_s_own_user_with_nothing_of_a_session_passes(tmp_path: Path) -> None:
    fake = OrchFake(users={"acme-orch": ORCH_HOME}, found=f"{ORCH_HOME}/owlcode/.git\n",
                    processes="  901 /usr/bin/python3 -I /usr/local/lib/orchestrator/service.py\n")
    rows = installer.service_user_lines(orchestrator_svc(tmp_path), fake)
    assert rows == [(True, "ok   the service's user acme-orch holds no harness, clone, Paseo daemon or SSH login: no session runs there")], \
        "[service] clone (~/owlcode) is the one clone it may hold: --check fetches it"
    find = next(c for c in fake.calls if "/usr/bin/find" in c)
    assert find[:5] == ["sudo", "-u", "acme-orch", "-H", "/usr/bin/find"], "as the user: root follows none of its links"
    assert f"{ORCH_HOME}/Library" in find and f"{ORCH_HOME}/.paseo/paseo.pid" in find and f"{ORCH_HOME}/.ssh/id_*" in find
    assert f"{ORCH_HOME}/.paseo" not in find, "a `paseo --host` call of the service writes ~/.paseo/cli-client-id"
    assert ["test", "-e", "/etc/ssh/sshd_config.d/000-acme-orch.conf"] in fake.calls


def test_the_orchestrator_s_own_user_fails_on_each_thing_of_a_session(tmp_path: Path) -> None:
    found = "".join(f"{ORCH_HOME}/{p}\n" for p in (".claude", ".pi", "product/.git", ".paseo/paseo.pid", ".ssh/authorized_keys",
                                                     ".ssh/id_ed25519"))
    fake = OrchFake(users={"acme-orch": ORCH_HOME}, found=found, sshd=True,
                    processes="  77 Paseo Supervisor\n  78 Paseo Daemon   \n  79 /bin/zsh\n")
    rows = installer.service_user_lines(orchestrator_svc(tmp_path), fake)
    assert [line for _, line in rows] == [
        "FAIL the service's user acme-orch holds a harness config (/Users/acme-orch/.claude): no session may run there",
        "FAIL the service's user acme-orch holds a harness config (/Users/acme-orch/.pi): no session may run there",
        "FAIL the service's user acme-orch holds a clone (/Users/acme-orch/product): no session may run there",
        "FAIL the service's user acme-orch holds a Paseo daemon (/Users/acme-orch/.paseo/paseo.pid): no session may run there",
        "FAIL the service's user acme-orch holds an SSH login (/Users/acme-orch/.ssh/authorized_keys): no session may run there",
        "FAIL the service's user acme-orch holds an SSH key (/Users/acme-orch/.ssh/id_ed25519): no session may run there",
        "FAIL the service's user acme-orch holds a Paseo daemon (process 78): no session may run there",
        "FAIL the service's user acme-orch holds an SSH login (/etc/ssh/sshd_config.d/000-acme-orch.conf): no session may run there",
    ]


def test_the_orchestrator_s_own_user_with_no_home_or_a_failed_search_fails(tmp_path: Path) -> None:
    rows = installer.service_user_lines(orchestrator_svc(tmp_path), OrchFake(users={}))
    assert rows == [(False, "FAIL the service's user acme-orch has no home: run `owlcode admin --orchestrator acme-orch`")]
    failed = subprocess.CompletedProcess([], 1, "", "find: /Users/acme-orch: Permission denied\n")
    rows = installer.service_user_lines(orchestrator_svc(tmp_path), OrchFake(users={"acme-orch": ORCH_HOME}, search=failed))
    assert rows == [(False, "FAIL the service's user acme-orch: cannot search its home (find: /Users/acme-orch: Permission denied)")]


def test_check_searches_only_the_user_that_admin_orchestrator_made(tmp_path: Path) -> None:
    """The marker is the user's full name, which only `admin --orchestrator` gives. Before the move, every product names its
    paseo_host and `[service] user` is still the agents' user that `owlcode admin` made, with the SSH login that the service
    needs to load after a restart: no search and no new line (round 1, R1-2). The setup of before gets none either."""
    before_move = orchestrator_svc(tmp_path)
    before_move = type(before_move)(**{**before_move.__dict__, "user": "agents"})
    fake = OrchFake(users={"agents": "/Users/agents"}, names={"agents": "agents agent"}, sshd=True,
                    found="/Users/agents/.ssh/authorized_keys\n/Users/agents/.claude\n")
    assert installer.service_user_lines(before_move, fake) == []
    assert not any("/usr/bin/find" in c or c[:2] == ["test", "-e"] for c in fake.calls), fake.calls
    assert ["dscl", ".", "-read", "/Users/agents", "RealName"] in fake.calls
    assert installer.service_user_lines(before_move, OrchFake(names={})) == [], "no such user: no line"
    fake = OrchFake(names={"agents": "agents agent"})
    assert installer.service_user_lines(svc(tmp_path), fake) == [], "the setup of before"
    code, said = _check(tmp_path, OrchFake(names={"agents": "agents agent"}))
    assert code == 0 and "the service's user" not in said[-1]


def test_check_ends_with_the_lines_of_the_orchestrator_s_own_user(tmp_path: Path) -> None:
    service_ = orchestrator_svc(tmp_path)
    fake = OrchFake(users={"acme-orch": ORCH_HOME, "acme-abc": "/Users/acme-abc", "acme-def": "/Users/acme-def"},
                    found=f"{ORCH_HOME}/.claude\n", id=subprocess.CompletedProcess([], 0, "503\n", ""), ps_user="acme-orch")
    said: list[str] = []
    assert installer.main(["check", "--config", str(service_.config)], run=fake, out=said.append) == 1
    assert said[-1].splitlines()[-1] == ("FAIL the service's user acme-orch holds a harness config (/Users/acme-orch/.claude): "
                                         "no session may run there")
