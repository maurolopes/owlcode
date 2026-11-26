"""Claude Code's adapter of the agents' guards (.claude/hooks/adapter.py): it turns a tool call into an action of
the guards' contract (.agents/guards/guard.py), and a refusal into Claude Code's (the message on stderr, exit status 2)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
ADAPTER = REPO / ".claude" / "hooks" / "adapter.py"


def call(event: object, python: str = sys.executable) -> subprocess.CompletedProcess[str]:
    text = event if isinstance(event, str) else json.dumps(event)
    return subprocess.run([python, str(ADAPTER)], input=text, capture_output=True, text=True, check=False)


@pytest.fixture
def main_checkout(tmp_path: Path) -> Path:
    """A main checkout of a repository on this workflow: the edit guard refuses its files."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "core.hooksPath", ".githooks"], check=True)
    return tmp_path


@pytest.mark.parametrize(("tool", "key"), [("Edit", "file_path"), ("Write", "file_path"), ("NotebookEdit", "notebook_path")])
def test_an_edit_in_the_main_checkout_is_refused(main_checkout: Path, tool: str, key: str) -> None:
    done = call({"tool_name": tool, "tool_input": {key: str(main_checkout / "app.py")}, "cwd": str(main_checkout)})
    assert done.returncode == 2 and "mise run start NAME" in done.stderr


def test_a_relative_path_is_read_from_the_calls_folder(main_checkout: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    assert call({"tool_name": "Write", "tool_input": {"file_path": "app.py"}, "cwd": str(main_checkout)}).returncode == 2
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    assert call({"tool_name": "Write", "tool_input": {"file_path": "app.py"}, "cwd": str(elsewhere)}).returncode == 0


def test_a_kill_by_name_is_refused_and_a_kill_by_pid_passes() -> None:
    done = call({"tool_name": "Bash", "tool_input": {"command": "pkill -f vite"}, "cwd": "/tmp"})
    assert done.returncode == 2 and "by their PID" in done.stderr
    assert call({"tool_name": "Bash", "tool_input": {"command": "kill 1234"}, "cwd": "/tmp"}).returncode == 0


@pytest.mark.parametrize("event", [
    "not json",
    [1],
    {"tool_input": {"command": "pkill x"}},  # no tool name
    {"tool_name": "Read", "tool_input": {"file_path": "/etc/hosts", "command": "pkill x"}},
    {"tool_name": "Bash", "tool_input": {}},
    {"tool_name": "Bash"},
    {"tool_name": "Edit", "tool_input": {"file_path": 3}},
])
def test_a_call_no_guard_reads_passes(event: object) -> None:
    done = call(event)
    assert done.returncode == 0 and done.stderr == "", event


def test_the_adapter_runs_on_the_macs_own_python() -> None:
    """settings.json runs `python3` from PATH, which can be macOS's 3.9."""
    if not Path("/usr/bin/python3").exists():
        pytest.skip("platform: no /usr/bin/python3 here")
    assert call({"tool_name": "Bash", "tool_input": {"command": "pkill x"}}, "/usr/bin/python3").returncode == 2


def test_claude_codes_settings_send_every_guarded_tool_to_the_adapter() -> None:
    hooks = json.loads((REPO / ".claude" / "settings.json").read_text())["hooks"]["PreToolUse"]
    commands = {entry["matcher"]: [hook["command"] for hook in entry["hooks"]] for entry in hooks}
    adapter = 'python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/adapter.py"'
    assert commands == {"Edit|Write|NotebookEdit": [adapter], "Bash": [adapter]}
