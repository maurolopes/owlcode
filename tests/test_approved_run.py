"""The owner's launcher for the scripts the owner runs (.workflow/approved_run.py): it shows each guarded file's
commits and diff since the owner's last yes, runs only after a yes, runs with no question when nothing changed, records
the yes in a file that only root can write, and never runs bytes other than the ones it showed. Each test works in a
git repository of its own; the run is a fake `execv`, and the record is the real WRITE_STATE step with no sudo."""

import ast
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("approved_run", REPO / ".workflow" / "approved_run.py")
assert SPEC and SPEC.loader
launcher = importlib.util.module_from_spec(SPEC)
sys.modules["approved_run"] = launcher
SPEC.loader.exec_module(launcher)

INSTALL, SERVICE, AGENT = "orchestrator/deploy/install.py", "orchestrator/deploy/service.py", ".workflow/agent_user.py"
TODAY = "2026-11-23"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@example.com", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def commit(repo: Path, message: str, **files: str) -> str:
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
        git(repo, "add", path)
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


class Run:
    """One launcher run with fakes: what it printed, what it asked, and what it ran."""

    def __init__(self, repo: Path, state: Path, answer: str = "y", on_ask=None) -> None:
        self.repo, self.state, self.answer, self.on_ask = repo, state, answer, on_ask
        self.lines: list[str] = []
        self.asked: list[str] = []
        self.ran: list[list[str]] = []

    def ask(self, prompt: str) -> str:
        self.asked.append(prompt)
        if self.on_ask:
            self.on_ask()
        return self.answer

    def __call__(self, *argv: str) -> int:
        return launcher.main(list(argv), repo=self.repo, state_path=self.state, uid=os.getuid(), ask=self.ask,
                             out=self.lines.append, record=self.record,
                             execv=lambda python, args: self.ran.append([python, *args]), today=TODAY)

    def record(self, path: Path, state: dict) -> None:
        launcher.record_state(path, state, python=sys.executable, sudo=[])

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "checkout"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    commit(root, "the first version", **{INSTALL: "print('install v1')\n", SERVICE: "print('service v1')\n",
                                         AGENT: "print('agent v1')\n"})
    return root


@pytest.fixture
def state(tmp_path: Path) -> Path:
    folder = tmp_path / "db"
    folder.mkdir(mode=0o755)
    return folder / "approved-runs.json"


def test_the_first_run_shows_each_whole_file_asks_once_and_a_yes_records_head_and_runs(repo, state):
    run = Run(repo, state)
    assert run(INSTALL, "--", "check") == 0
    head = git(repo, "rev-parse", "HEAD")
    for path, text in ((INSTALL, "install v1"), (SERVICE, "service v1"), (AGENT, "agent v1")):
        assert f"{path}: no yes recorded yet: the whole file at HEAD ({head[:7]})" in run.text and text in run.text
    assert run.asked == ["Run this version as root? [y/N] "], "install runs root's steps: one question for its three files"
    assert json.loads(state.read_text()) == {p: {"commit": head, "date": TODAY} for p in (INSTALL, SERVICE, AGENT)}
    assert oct(state.stat().st_mode & 0o777) == "0o644"
    assert run.ran == [["/usr/local/bin/python3", "/usr/local/bin/python3", "-I", str(repo / INSTALL), "check"]]


def test_a_no_runs_nothing_and_records_nothing(repo, state):
    for answer in ("n", "", "no", "maybe"):
        run = Run(repo, state, answer=answer)
        assert run(INSTALL, "--") == 1
        assert run.ran == [] and not state.exists(), answer
        assert run.lines[-1] == "No: nothing ran, and your last yes stays as it was."


def test_with_no_change_since_the_last_yes_it_runs_with_no_question(repo, state):
    assert Run(repo, state)(INSTALL, "--") == 0
    commit(repo, "a change elsewhere", **{"app/x.py": "x = 1\n"})  # HEAD moves, the guarded files do not
    run = Run(repo, state, on_ask=lambda: pytest.fail("asked with no change"))
    assert run(INSTALL, "--", "restart") == 0
    assert run.lines == [] and run.ran[0][-1] == "restart"


def test_a_change_shows_its_commits_and_diff_since_the_last_yes_and_a_yes_records_the_new_head(repo, state):
    Run(repo, state)(INSTALL, "--")
    first = git(repo, "rev-parse", "HEAD")
    state.write_text(json.dumps({**json.loads(state.read_text()), INSTALL: {"commit": first, "date": "2026-11-20"}}))
    commit(repo, "ABC-1210: halt new sessions when run setup fails", **{INSTALL: "print('install v2')\n"})
    commit(repo, "ABC-1211: one more step", **{INSTALL: "print('install v3')\n"})
    head = git(repo, "rev-parse", "HEAD")
    run = Run(repo, state)
    assert run(INSTALL, "--") == 0
    text = run.text
    assert text.startswith(f"{INSTALL}: 2 commits since your last yes ({first[:7]}, 2026-11-20)\n")
    assert f"  {head[:7]} ABC-1211: one more step" in text and "ABC-1210: halt new sessions when run setup fails" in text
    assert "-print('install v1')\n+print('install v3')" in text
    assert SERVICE not in text and AGENT not in text, "an unchanged file shows nothing"
    assert json.loads(state.read_text())[INSTALL] == {"commit": head, "date": TODAY}
    commit(repo, "ABC-1212: one line", **{AGENT: "print('agent v2')\n"})
    run = Run(repo, state, answer="n")
    assert run(INSTALL, "--") == 1
    assert run.text.startswith(f"{AGENT}: 1 commit since your last yes ({head[:7]}, {TODAY})\n"), "one commit, no plural"


def test_a_file_that_changes_after_its_diff_is_shown_does_not_run(repo, state):
    run = Run(repo, state, on_ask=lambda: (repo / SERVICE).write_text("print('swapped')\n"))
    assert run(INSTALL, "--") == 1
    assert run.ran == [] and run.lines[-1] == f"{SERVICE} changed after its diff was shown: nothing ran. Run the command again"


def test_a_file_that_differs_from_head_is_refused_before_any_diff(repo, state):
    (repo / AGENT).write_text("print('not committed')\n")
    run = Run(repo, state, on_ask=lambda: pytest.fail("asked about code that is not committed"))
    assert run(AGENT, "--", "models") == 1
    head = git(repo, "rev-parse", "HEAD")
    assert run.lines == [f"{AGENT} differs from HEAD ({head[:7]}): commit it or restore it. Only committed code is shown and "
                         "run: nothing ran"]
    assert run.ran == [] and not state.exists()


def test_agent_user_covers_only_itself_and_asks_as_the_caller(repo, state):
    run = Run(repo, state)
    assert run(AGENT, "--", "models") == 0
    assert INSTALL not in run.text and SERVICE not in run.text
    expected = ["Run this version? [y/N] "] if os.geteuid() else ["Run this version as root? [y/N] "]
    assert run.asked == expected
    assert run.ran == [["/usr/bin/python3", "/usr/bin/python3", "-I", str(repo / AGENT), "models"]]
    assert list(json.loads(state.read_text())) == [AGENT]
    # install still shows its own two files, and not agent_user.py, which the yes above covers
    run = Run(repo, state, answer="n")
    run(INSTALL, "--")
    assert INSTALL in run.text and SERVICE in run.text and AGENT not in run.text


def test_a_last_yes_on_a_commit_the_clone_lacks_shows_the_whole_file(repo, state):
    state.write_text(json.dumps({AGENT: {"commit": "a" * 40, "date": "2026-11-01"}}))
    run = Run(repo, state, answer="n")
    assert run(AGENT, "--") == 1
    assert run.text.startswith(f"{AGENT}: your last yes (aaaaaaa, 2026-11-01) names a commit that this clone lacks")
    assert "print('agent v1')" in run.text


def test_control_and_invisible_characters_print_as_escapes(repo, state):
    Run(repo, state)(AGENT, "--")
    commit(repo, "hide\x1b[2K a line", **{AGENT: "print('agent v1')\nx = 1  # \x1b[1A\x1b[2K\r‮​\n"})
    run = Run(repo, state, answer="n")
    run(AGENT, "--")
    assert "\x1b" not in run.text and "‮" not in run.text and "\r" not in run.text and "​" not in run.text
    assert "+x = 1  # \\x1b[1A\\x1b[2K\\x0d\\u202e\\u200b" in run.text
    assert launcher.visible("a\tb\nc é ✓".encode()) == "a\tb\nc é ✓", "tab, newline and printable text stay"
    assert launcher.visible(b"\xff") == "\\xff", "bytes that are not UTF-8 show as escapes too"



def test_no_program_from_the_checkouts_git_config_runs(repo, state, tmp_path):
    """R1-1: with `log.showSignature` on, `git log` runs `gpg.program` for a signed commit, as root under sudo, before the
    question. Code that runs as the owner can write .git/config: the launcher's git calls run no such program."""
    Run(repo, state)(AGENT, "--")
    touched = tmp_path / "gpg-ran"
    program = tmp_path / "gpg.sh"
    program.write_text(f"#!/bin/sh\ntouch {touched}\nexit 1\n")
    program.chmod(0o755)
    git(repo, "config", "log.showSignature", "true")
    git(repo, "config", "gpg.program", str(program))
    commit(repo, "a signed change", **{AGENT: "print('agent v2')\n"})
    raw = git(repo, "cat-file", "commit", "HEAD") + "\n"
    head, body = raw.split("\n\n", 1)
    signed = (head + "\ngpgsig -----BEGIN PGP SIGNATURE-----\n \n iQ==\n -----END PGP SIGNATURE-----\n\n" + body)
    new = subprocess.run(["git", "-C", str(repo), "hash-object", "-t", "commit", "-w", "--stdin"], input=signed, text=True,
                         check=True, capture_output=True).stdout.strip()
    git(repo, "update-ref", "HEAD", new)
    git(repo, "log", "--oneline", "-1")  # the control: plain git runs the program
    assert touched.exists(), "the test's set-up runs gpg.program"
    touched.unlink()
    run = Run(repo, state, answer="n")
    assert run(AGENT, "--") == 1
    assert "1 commit since your last yes" in run.text and "a signed change" in run.text
    assert not touched.exists(), "the launcher ran gpg.program"


def test_an_attribute_that_marks_a_guarded_file_binary_does_not_hide_its_diff(repo, state):
    """R1-2: `-diff` in a committed .gitattributes (it merges on the verdict) would make git print only `Binary files ...
    differ`: the launcher prints the lines."""
    Run(repo, state)(AGENT, "--")
    commit(repo, "hide the diff", **{".gitattributes": f"{AGENT} -diff\n", AGENT: "print('agent v2')\n"})
    run = Run(repo, state, answer="n")
    run(AGENT, "--")
    assert "Binary files" not in run.text
    assert "-print('agent v1')\n+print('agent v2')" in run.text

@pytest.mark.parametrize("damage", ["file mode", "folder mode", "link", "owner", "commit", "shape"])
def test_a_state_file_that_another_user_could_write_or_that_is_damaged_is_refused(repo, state, tmp_path, damage):
    Run(repo, state)(AGENT, "--")
    uid = os.getuid()
    if damage == "file mode":
        state.chmod(0o664)
    elif damage == "folder mode":
        state.parent.chmod(0o775)
    elif damage == "link":
        real = tmp_path / "elsewhere.json"
        shutil.move(state, real)
        state.symlink_to(real)
    elif damage == "owner":
        uid += 1  # the file is the test user's, and the launcher expects another owner (root in a real run)
    elif damage == "commit":
        state.write_text(json.dumps({AGENT: {"commit": "--output=/tmp/x", "date": TODAY}}))
    else:
        state.write_text("[]")
    lines: list[str] = []
    ran: list[list[str]] = []
    code = launcher.main([AGENT, "--"], repo=repo, state_path=state, uid=uid, ask=lambda _: pytest.fail("asked"),
                         out=lines.append, record=lambda *_: pytest.fail("recorded"),
                         execv=lambda python, args: ran.append(args))
    assert code == 1 and ran == [] and len(lines) == 1
    assert ("only root can write" in lines[0] or "without following a link" in lines[0]
            or "is not a record of yes answers" in lines[0]), lines


def test_a_missing_state_folder_or_bad_arguments_run_nothing(repo, tmp_path):
    lines: list[str] = []
    code = launcher.main([AGENT, "--"], repo=repo, state_path=tmp_path / "none" / "x.json", uid=os.getuid(),
                         ask=lambda _: "y", out=lines.append, execv=lambda *_: pytest.fail("ran"))
    assert code == 1 and lines == [f"cannot read the folder {tmp_path / 'none'} of the record of your yes answers: nothing ran"]
    for argv in ([], ["app/cli.py", "--"], [AGENT, "models"]):
        lines = []
        assert launcher.main(argv, repo=repo, out=lines.append, execv=lambda *_: pytest.fail("ran")) == 2
        assert lines[0].startswith("usage: approved_run.py FILE [-- ARGS...]"), argv


def test_the_record_step_writes_a_whole_file_that_the_owner_can_read(state):
    launcher.record_state(state, {AGENT: {"commit": "b" * 40, "date": TODAY}}, python=sys.executable, sudo=[])
    assert oct(state.stat().st_mode & 0o777) == "0o644"
    assert launcher.read_state(state, os.getuid()) == {AGENT: {"commit": "b" * 40, "date": TODAY}}
    assert [p.name for p in state.parent.iterdir()] == [state.name], "no temporary file is left"
    with pytest.raises(launcher.Refused, match="could not record your yes"):
        launcher.record_state(state.parent / "missing" / "x.json", {}, python=sys.executable, sudo=[])


def test_the_guarded_files_and_the_state_are_the_issues():
    assert launcher.GUARDED == tuple(sorted((INSTALL, SERVICE, AGENT)))
    assert REPO not in launcher.STATE.parents, "the record is outside the repository"
    # install.py copies these two into root's installed folder: its run shows them too
    installed = {source for source in ast.literal_eval(
        (REPO / INSTALL).read_text().split("FILES = ", 1)[1].split("\n}\n", 1)[0] + "\n}") if source in launcher.GUARDED}
    assert installed <= set(launcher.SCRIPTS[INSTALL].covers)


def test_the_launcher_parses_as_python_3_9():
    """`/usr/bin/python3` is 3.9 on the Mac and runs the launcher: Ruff and mypy check 3.14 only."""
    ast.parse((REPO / ".workflow" / "approved_run.py").read_text(), feature_version=(3, 9))


def test_a_whole_run_on_the_systems_python(repo, state):
    """The flow end to end on `/usr/bin/python3` (3.9 on the Mac): a first yes, then a run with no question."""
    if not Path("/usr/bin/python3").exists():
        pytest.skip("no /usr/bin/python3 here")
    code = f"""
import importlib.util, os, sys
spec = importlib.util.spec_from_file_location("approved_run", {str(REPO / '.workflow' / 'approved_run.py')!r})
m = importlib.util.module_from_spec(spec); sys.modules["approved_run"] = m; spec.loader.exec_module(m)
from pathlib import Path
ran = []
answers = iter(["y"])
for _ in range(2):
    code = m.main([{AGENT!r}, "--", "models"], repo=Path({str(repo)!r}), state_path=Path({str(state)!r}), uid=os.getuid(),
                  ask=lambda p: next(answers), out=lambda s: None, execv=lambda p, a: ran.append(a),
                  record=lambda path, st: m.record_state(path, st, sudo=[]))
    assert code == 0, code
assert len(ran) == 2, ran
print(sys.version_info[:2])
"""
    done = subprocess.run(["/usr/bin/python3", "-I", "-c", code], check=False, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_a_missing_interpreter_or_no_terminal_runs_nothing(repo, state, monkeypatch):
    Run(repo, state)(AGENT, "--")
    lines: list[str] = []

    def no_python(python: str, args: list[str]) -> None:
        raise FileNotFoundError(2, "No such file or directory")

    assert launcher.main([AGENT, "--"], repo=repo, state_path=state, uid=os.getuid(), out=lines.append, execv=no_python) == 1
    assert lines == [f"cannot run /usr/bin/python3 (No such file or directory): {AGENT} needs it"]
    # a change with no terminal to answer at: no question, nothing recorded, nothing run
    commit(repo, "a change", **{AGENT: "print('agent v2')\n"})
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))  # not a terminal
    lines = []
    assert launcher.main([AGENT, "--"], repo=repo, state_path=state, uid=os.getuid(), out=lines.append,
                         record=lambda *_: pytest.fail("recorded"), execv=lambda *_: pytest.fail("ran")) == 1
    assert lines[-1] == ("a guarded file changed since your last yes: run the command at a terminal, read the diff and answer: "
                         "nothing ran")


def test_the_record_is_named_after_the_repository_or_agent_user(tmp_path: Path) -> None:
    """The record's file is /var/db/NAME-approved-runs.json: NAME is AGENT_USER, else the name of the repository that
    `origin` names, read from git's config, also in a worktree. A checkout with neither stops the launcher."""
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    (checkout / ".git" / "config").write_text('[core]\n\tbare = false\n[remote "origin"]\n\turl = git@github.com:acme/widget-app.git\n')
    assert launcher.state_path({}, checkout) == Path("/var/db/widget-app-approved-runs.json")
    assert launcher.state_path({"AGENT_USER": "other"}, checkout) == Path("/var/db/other-approved-runs.json")
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (checkout / ".git" / "worktrees" / "w").mkdir(parents=True)
    (checkout / ".git" / "worktrees" / "w" / "commondir").write_text("../..\n")
    (worktree / ".git").write_text(f"gitdir: {checkout / '.git' / 'worktrees' / 'w'}\n")
    assert launcher.state_path({}, worktree) == Path("/var/db/widget-app-approved-runs.json")
    bare = tmp_path / "none"
    (bare / ".git").mkdir(parents=True)
    (bare / ".git" / "config").write_text("[core]\n\tbare = false\n")
    with pytest.raises(SystemExit, match="no name for the record"):
        launcher.state_path({}, bare)
    for refused in ("a/b", "Proj_C"):
        with pytest.raises(SystemExit, match="cannot name the record"):
            launcher.state_path({"AGENT_USER": refused}, checkout)


def test_the_launcher_names_the_record_as_agent_user_names_its_user(tmp_path: Path) -> None:
    """The launcher copies agent_user.py's name rule and its reading of `origin` (it never imports the file it guards):
    the two copies must agree, or the launcher would name another record."""
    saved = os.environ.get("AGENT_USER")
    os.environ["AGENT_USER"] = "acme-agent"
    try:
        spec = importlib.util.spec_from_file_location("agent_user_copy", REPO / ".workflow" / "agent_user.py")
        assert spec and spec.loader
        agent = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(agent)
    finally:
        if saved is None:
            del os.environ["AGENT_USER"]
        else:
            os.environ["AGENT_USER"] = saved
    assert launcher.NAME_RULE.pattern == agent.NAME_RULE.pattern
    assert launcher.NAME_RULE_TEXT == agent.NAME_RULE_TEXT
    plain = tmp_path / "plain"
    (plain / ".git").mkdir(parents=True)
    (plain / ".git" / "config").write_text('[remote "origin"]\n\turl = https://github.com/acme/widget-app.git/\n')
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (plain / ".git" / "worktrees" / "w").mkdir(parents=True)
    (plain / ".git" / "worktrees" / "w" / "commondir").write_text("../..\n")
    (worktree / ".git").write_text(f"gitdir: {plain / '.git' / 'worktrees' / 'w'}\n")
    none = tmp_path / "none"
    (none / ".git").mkdir(parents=True)
    (none / ".git" / "config").write_text("[core]\n\tbare = false\n")
    for checkout in (plain, worktree, none):
        assert launcher.origin_name(checkout) == agent.origin_name(checkout), checkout
    assert launcher.origin_name(plain) == "widget-app" and launcher.origin_name(none) == ""
