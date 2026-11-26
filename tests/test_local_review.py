"""The local review of .github/local_review.py. A fake command stands for the model: no network, no real model."""

from __future__ import annotations

import importlib.util
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("local_review", REPO / ".github" / "local_review.py")
assert spec and spec.loader
local_review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(local_review)

FINDING = {"file": "app/x.py", "line": 12, "summary": "The new branch has no test.",
           "evidence": "app/x.py:12 `if late:`; no test names `late`", "fix": "Add a test with late=True.",
           "confidence": "high"}


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false",
                    *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repository whose origin/main is one commit behind HEAD, with one changed line between them."""
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "work")
    (root / "a.py").write_text("x = 1\n")
    git(root, "add", "a.py")
    git(root, "commit", "-q", "-m", "base")
    git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    (root / "a.py").write_text("x = 2  # ``` a fence in the diff\n")
    git(root, "commit", "-q", "-am", "change")
    return root


def fake(tmp_path: Path, body: str) -> str:
    """A command that saves its standard input and its schema variable to files, then runs `body` (Python)."""
    script = tmp_path / "fake.py"
    script.write_text(
        "import os, sys, pathlib\n"
        f"here = pathlib.Path({str(tmp_path)!r})\n"
        "(here / 'prompt.txt').write_text(sys.stdin.read())\n"
        "(here / 'schema.json').write_text(os.environ.get('LOCAL_REVIEW_SCHEMA', ''))\n"
        "(here / 'cwd.txt').write_text(os.getcwd())\n" + body)
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(script))}"


def run(tmp_path: Path, repo: Path, command: str | None, *, note: bool = True, timeout: float = 30) -> list[str]:
    issue = tmp_path / "issue.md"
    issue.write_text("## What to do\n\n- [ ] Make x two.\n")
    note_file = tmp_path / "note.md"
    note_file.write_text("It leaves the docs for ABC-1.\n")
    environ: dict[str, str] = {"PATH": "/usr/bin:/bin"}
    if command is not None:
        environ[local_review.COMMAND_VARIABLE] = command
    try:
        lines: list[str] = local_review.review(issue, note_file if note else None, "origin/main", repo, environ, timeout)
    except local_review.DidNotRun as exc:
        return [f"Local review: did not run ({exc})"]
    return lines


@pytest.mark.real_clock("runs git and a fake command as subprocesses")
def test_metrics_run_reaches_review_command(tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    body = "print('{\"findings\": [], \"checked\": [\"x\"]}')\n"
    command = fake(tmp_path, "(here / 'metrics.txt').write_text(os.environ['AGENT_METRICS_RUN'])\n" + body)
    monkeypatch.chdir(repo)
    monkeypatch.setenv(local_review.COMMAND_VARIABLE, command)
    issue = tmp_path / "issue.md"
    issue.write_text("Check x.")
    run_id = "ABC-123-20261122T123456Z-test"
    assert local_review.main(["--issue-file", str(issue), "--metrics-run", run_id]) == 0
    assert (tmp_path / "metrics.txt").read_text() == run_id


def reply_body(reply: Any) -> str:
    return f"print({json.dumps(json.dumps(reply))})\n"


def test_a_result_without_model_usage_reads_the_aggregate_numbers() -> None:
    value = {"type": "result", "is_error": False, "duration_ms": 2000,
             "usage": {"input_tokens": 10, "cache_read_input_tokens": 80,
                       "cache_creation_input_tokens": 10, "output_tokens": 100}}
    row = local_review.claude_review_row(json.dumps(value), 1000, 3000)
    assert row is not None
    assert (row["model"], row["input"], row["cached"], row["output"], row["cost"]) == ("unknown", 100, 80, 100, None)
    assert row["tokens_per_s"] == 50 and row["ttft_s"] is None


@pytest.mark.real_clock("runs git and a fake command as subprocesses")
def test_a_failed_claude_review_records_its_error_before_the_exit(tmp_path: Path, repo: Path,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    wrapped = {
        "type": "result", "subtype": "error", "is_error": True, "result": "private error",
        "duration_ms": 4000, "usage": {"input_tokens": 10, "output_tokens": 100},
        "modelUsage": {"claude-x": {"inputTokens": 10, "outputTokens": 100}},
    }
    command = fake(tmp_path, f"print({json.dumps(json.dumps(wrapped))})\nsys.exit(3)\n")
    monkeypatch.chdir(repo)
    monkeypatch.setenv(local_review.COMMAND_VARIABLE, command)
    issue = tmp_path / "issue.md"
    issue.write_text("Check x.")
    run_id = "ABC-123-20261122T123456Z-test"
    assert local_review.main(["--issue-file", str(issue), "--metrics-run", run_id]) == 0
    row = json.loads((tmp_path / ".local/state/agent-metrics" / f"{run_id}.jsonl").read_text())
    assert row["error"] == "api_error" and row["tokens_per_s"] is None
    assert "private" not in json.dumps(row)


@pytest.mark.real_clock("runs git and a fake command as subprocesses")
def test_a_metrics_write_that_fails_still_gives_the_findings(tmp_path: Path, repo: Path,
                                                            monkeypatch: pytest.MonkeyPatch,
                                                            capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    state = tmp_path / ".local/state"
    state.mkdir(parents=True)
    (state / "agent-metrics").write_text("a file, so the folder cannot be made")
    wrapped = {"type": "result", "is_error": False, "duration_ms": 2000,
               "usage": {"output_tokens": 100}, "structured_output": {"findings": [], "checked": []}}
    command = fake(tmp_path, f"print({json.dumps(json.dumps(wrapped))})\n")
    monkeypatch.chdir(repo)
    monkeypatch.setenv(local_review.COMMAND_VARIABLE, command)
    issue = tmp_path / "issue.md"
    issue.write_text("Check x.")
    assert local_review.main(["--issue-file", str(issue), "--metrics-run", "ABC-123-20261122T123456Z-test"]) == 0
    assert capsys.readouterr().out == "Local review: 0 findings\n"


@pytest.mark.real_clock("runs git and a fake command as subprocesses")
def test_a_claude_code_review_records_its_numbers_and_no_content(tmp_path: Path, repo: Path,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    wrapped = {
        "type": "result", "subtype": "success", "is_error": False, "result": "private reply",
        "duration_ms": 4000, "duration_api_ms": 2000, "total_cost_usd": 0.5,
        "usage": {"input_tokens": 10, "cache_read_input_tokens": 80,
                  "cache_creation_input_tokens": 10, "output_tokens": 100},
        "modelUsage": {"claude-x": {"inputTokens": 10, "outputTokens": 100, "cacheReadInputTokens": 80,
                                     "cacheCreationInputTokens": 10, "costUSD": 0.5}},
        "structured_output": {"findings": [], "checked": []},
    }
    command = fake(tmp_path, f"print({json.dumps(json.dumps(wrapped))})\n")
    monkeypatch.chdir(repo)
    monkeypatch.setenv(local_review.COMMAND_VARIABLE, command)
    issue = tmp_path / "issue.md"
    issue.write_text("Check x.")
    run_id = "ABC-123-20261122T123456Z-test"
    assert local_review.main(["--issue-file", str(issue), "--metrics-run", run_id]) == 0
    state = tmp_path / ".local/state/agent-metrics" / f"{run_id}.jsonl"
    rows = [json.loads(line) for line in state.read_text().splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert (row["role"], row["provider"], row["model"]) == ("review", "claude-code", "claude-x")
    assert (row["input"], row["cached"], row["output"], row["cost"]) == (100, 80, 100, 0.5)
    assert row["ttft_s"] is None and row["timing"] == "invocation" and row["tokens_per_s"] == 50
    assert "private" not in state.read_text(), "the row holds no reply"


def test_a_review_with_two_models_sums_their_tokens() -> None:
    result = {"type": "result", "is_error": False, "duration_api_ms": 2000, "total_cost_usd": 0.7,
              "modelUsage": {"big": {"inputTokens": 10, "outputTokens": 100, "cacheReadInputTokens": 80,
                                     "cacheCreationInputTokens": 10},
                             "small": {"inputTokens": 5, "outputTokens": 50}}}
    row = local_review.claude_review_row(json.dumps(result), 0, 1)
    assert row is not None
    assert (row["model"], row["input"], row["cached"], row["output"], row["cost"]) == ("big", 105, 80, 150, 0.7)
    assert row["tokens_per_s"] == 75


@pytest.mark.real_clock("runs git and a fake command as subprocesses")
def test_another_command_s_json_writes_no_review_row(tmp_path: Path, repo: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    command = fake(tmp_path, reply_body({"findings": [], "checked": []}))
    monkeypatch.chdir(repo)
    monkeypatch.setenv(local_review.COMMAND_VARIABLE, command)
    issue = tmp_path / "issue.md"
    issue.write_text("Check x.")
    run_id = "ABC-123-20261122T123456Z-test"
    assert local_review.main(["--issue-file", str(issue), "--metrics-run", run_id]) == 0
    assert not (tmp_path / ".local/state/agent-metrics" / f"{run_id}.jsonl").exists()


@pytest.mark.real_clock("runs git and a fake command as subprocesses")
def test_the_prompt_holds_the_brief_the_issue_the_note_and_the_diff_in_that_order(tmp_path: Path, repo: Path) -> None:
    lines = run(tmp_path, repo, fake(tmp_path, reply_body({"findings": [], "checked": ["x"]})))
    assert lines == ["Local review: 0 findings"]
    prompt = (tmp_path / "prompt.txt").read_text()
    brief = (REPO / "agents" / "local-review.md").read_text().rstrip()
    assert prompt.startswith(brief)
    parts = ["## The issue", "Make x two.", "## What the change leaves out", "It leaves the docs for ABC-1.",
             "## The diff", "+x = 2"]
    places = [prompt.index(part) for part in parts]
    assert places == sorted(places)
    assert "\n````diff\n" in prompt, "the fence is longer than the backticks in the diff"
    assert json.loads((tmp_path / "schema.json").read_text()) == local_review.SCHEMA
    assert Path((tmp_path / "cwd.txt").read_text()).resolve() == repo.resolve(), "the command runs at the repository's top"


@pytest.mark.real_clock("runs git and a fake command as subprocesses")
def test_with_no_note_the_prompt_says_so(tmp_path: Path, repo: Path) -> None:
    run(tmp_path, repo, fake(tmp_path, reply_body({"findings": [], "checked": []})), note=False)
    assert f"## What the change leaves out\n\n{local_review.NO_NOTE}\n" in (tmp_path / "prompt.txt").read_text()


@pytest.mark.real_clock("runs git and a fake command as subprocesses")
@pytest.mark.parametrize("wrapped", [False, True])
def test_the_findings_are_printed_from_a_bare_or_a_wrapped_reply(tmp_path: Path, repo: Path, wrapped: bool) -> None:
    """Claude Code's `--output-format json` puts the object under `structured_output` (the issue's Agent notes)."""
    reply: dict[str, Any] = {"inventory": ["a.py"], "findings": [FINDING, {**FINDING, "line": 3, "confidence": "medium"}],
                             "checked": ["the docstring"]}
    body = {"type": "result", "is_error": False, "structured_output": reply} if wrapped else reply
    lines = run(tmp_path, repo, fake(tmp_path, reply_body(body)))
    assert lines == [
        "1. app/x.py:12 (high) The new branch has no test.",
        "   Evidence: app/x.py:12 `if late:`; no test names `late`",
        "   Fix: Add a test with late=True.",
        "2. app/x.py:3 (medium) The new branch has no test.",
        "   Evidence: app/x.py:12 `if late:`; no test names `late`",
        "   Fix: Add a test with late=True.",
        "Local review: 2 findings",
    ]


GOOD: dict[str, Any] = {"findings": [FINDING], "checked": ["x"]}
ONE_FINDING = ["1. app/x.py:12 (high) The new branch has no test.",
               "   Evidence: app/x.py:12 `if late:`; no test names `late`",
               "   Fix: Add a test with late=True.", "Local review: 1 finding"]


@pytest.mark.real_clock("runs git and a fake command as subprocesses")
@pytest.mark.parametrize("printed", [
    "Here is my review.\n```json\n" + json.dumps(GOOD) + "\n```\nDone.",
    "```\n" + json.dumps(GOOD) + "\n```",
    "I checked {the diff} first.\n" + json.dumps({"note": 1}) + "\nThe answer: " + json.dumps(GOOD) + "\nThanks.",
    json.dumps({"result": "ok", "structured_output": GOOD}),
    "  " + json.dumps(GOOD, indent=2) + "\n",
])
def test_the_reply_is_found_in_any_printed_text(tmp_path: Path, repo: Path, printed: str) -> None:
    assert run(tmp_path, repo, fake(tmp_path, f"print({printed!r})\n")) == ONE_FINDING


@pytest.mark.real_clock("runs git and a fake command as subprocesses")
def test_the_last_fitting_object_wins_over_an_earlier_one(tmp_path: Path, repo: Path) -> None:
    early = {"findings": [{**FINDING, "summary": "An old thought."}], "checked": []}
    printed = f"First: {json.dumps(early)}\nFinal: {json.dumps(GOOD)}"
    assert run(tmp_path, repo, fake(tmp_path, f"print({printed!r})\n")) == ONE_FINDING


@pytest.mark.real_clock("runs git and a fake command as subprocesses")
def test_a_reply_with_no_json_runs_the_command_once_more_with_the_json_only_line(tmp_path: Path, repo: Path) -> None:
    body = ("count = here / 'count.txt'\n"
            "n = int(count.read_text()) + 1 if count.exists() else 1\n"
            "count.write_text(str(n))\n"
            f"print('prose only' if n == 1 else {json.dumps(GOOD)!r})\n")
    lines = run(tmp_path, repo, fake(tmp_path, body))
    assert lines == ONE_FINDING
    assert (tmp_path / "count.txt").read_text() == "2"
    assert local_review.RETRY_LINE in (tmp_path / "prompt.txt").read_text(), "the second prompt ends with the short line"


@pytest.mark.real_clock("runs git and a fake command as subprocesses")
def test_a_reply_with_no_valid_json_twice_gives_the_did_not_run_line_after_one_retry(tmp_path: Path, repo: Path) -> None:
    body = ("count = here / 'count.txt'\n"
            "count.write_text(str(int(count.read_text()) + 1 if count.exists() else 1))\n"
            "print('no json here')\n")
    assert run(tmp_path, repo, fake(tmp_path, body)) == ["Local review: did not run (no valid reply: the reply is not JSON)"]
    assert (tmp_path / "count.txt").read_text() == "2"


@pytest.mark.real_clock("runs git and a fake command as subprocesses")
def test_a_retry_that_fails_gives_the_command_s_own_reason(tmp_path: Path, repo: Path) -> None:
    body = ("count = here / 'count.txt'\n"
            "n = int(count.read_text()) + 1 if count.exists() else 1\n"
            "count.write_text(str(n))\n"
            "sys.exit(3) if n == 2 else print('prose only')\n")
    assert run(tmp_path, repo, fake(tmp_path, body)) == ["Local review: did not run (the command exited with 3)"]


@pytest.mark.real_clock("runs git and a fake command as subprocesses")
def test_a_harness_error_result_is_not_run_again(tmp_path: Path, repo: Path) -> None:
    """Claude Code prints its own error result with `is_error: true` when a budget or turn limit stops it, and a
    second run meets the same limit. Measured 2026-11-23 on 2.1.328: `--max-budget-usd` exits 1, so no retry runs today;
    this keeps that rule for a harness that exits 0."""
    body = ("count = here / 'count.txt'\n"
            "count.write_text(str(int(count.read_text()) + 1 if count.exists() else 1))\n"
            + reply_body({"type": "result", "subtype": "error_max_budget_usd", "is_error": True}))
    assert run(tmp_path, repo, fake(tmp_path, body)) == [
        "Local review: did not run (the command reported an error (error_max_budget_usd))"]
    assert (tmp_path / "count.txt").read_text() == "1", "the command ran once"


@pytest.mark.real_clock("runs git and a fake command as subprocesses")
def test_a_failed_command_is_not_run_again(tmp_path: Path, repo: Path) -> None:
    body = ("count = here / 'count.txt'\n"
            "count.write_text(str(int(count.read_text()) + 1 if count.exists() else 1))\n"
            "sys.exit(3)\n")
    assert run(tmp_path, repo, fake(tmp_path, body)) == ["Local review: did not run (the command exited with 3)"]
    assert (tmp_path / "count.txt").read_text() == "1"


@pytest.mark.real_clock("runs git and a fake command as subprocesses")
def test_the_prompt_asks_for_the_json_only_and_holds_the_schema(tmp_path: Path, repo: Path) -> None:
    run(tmp_path, repo, fake(tmp_path, reply_body(GOOD)))
    prompt = (tmp_path / "prompt.txt").read_text()
    tail = prompt[prompt.index("## Your reply"):]
    assert "one JSON object only" in tail and json.dumps(local_review.SCHEMA, indent=1) in tail
    assert prompt.index("## The diff") < prompt.index("## Your reply")


@pytest.mark.real_clock("runs git and a fake command as subprocesses")
def test_one_finding_is_singular(tmp_path: Path, repo: Path) -> None:
    lines = run(tmp_path, repo, fake(tmp_path, reply_body({"findings": [FINDING], "checked": []})))
    assert lines[-1] == "Local review: 1 finding"


@pytest.mark.real_clock("runs git and a fake command as subprocesses")
@pytest.mark.parametrize(("body", "reason"), [
    ("sys.exit(3)\n", "the command exited with 3"),
    ("print('not json')\n", "no valid reply: the reply is not JSON"),
    (reply_body({"findings": []}), "no valid reply: the reply does not fit the schema: no list checked"),
    (reply_body([]), "no valid reply: the reply does not fit the schema: the reply is not an object"),
    (reply_body({"findings": [], "checked": [], "verdict": "ok"}), "no valid reply: the reply does not fit the schema: unknown key verdict"),
    (reply_body({"findings": [{**FINDING, "confidence": "low"}], "checked": []}),
     "no valid reply: the reply does not fit the schema: finding 1: confidence is not high or medium"),
    (reply_body({"findings": [{**FINDING, "line": "12"}], "checked": []}),
     "no valid reply: the reply does not fit the schema: finding 1: line is not int"),
    (reply_body({"findings": [{k: v for k, v in FINDING.items() if k != "fix"}], "checked": []}),
     "no valid reply: the reply does not fit the schema: finding 1 does not have the keys file, line, summary, evidence, fix, confidence"),
    (reply_body({"findings": [], "checked": [1]}), "no valid reply: the reply does not fit the schema: checked holds an item that is not text"),
    (reply_body({"findings": [], "checked": [], "inventory": 5}), "no valid reply: the reply does not fit the schema: inventory is not a list"),
    (reply_body({"findings": [], "checked": [], "inventory": None}), "no valid reply: the reply does not fit the schema: inventory is not a list"),
])
def test_each_failure_gives_the_did_not_run_line(tmp_path: Path, repo: Path, body: str, reason: str) -> None:
    assert run(tmp_path, repo, fake(tmp_path, body)) == [f"Local review: did not run ({reason})"]


@pytest.mark.real_clock("runs git as a subprocess")
@pytest.mark.parametrize("command", [None, "", "  "])
def test_with_no_command_set_the_review_does_not_run(tmp_path: Path, repo: Path, command: str | None) -> None:
    assert run(tmp_path, repo, command) == ["Local review: did not run (no LOCAL_REVIEW_COMMAND set)"]


def stopped(pid: int) -> bool:
    """The process is gone, or a zombie: killed, but its parent did not reap it. An orphan's parent is PID 1, and in a CI
    container PID 1 (`tail -f /dev/null`) reaps nothing, so a killed orphan stays a zombie there, and `kill(pid, 0)` still
    finds it (OWL-48)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    proc = Path(f"/proc/{pid}/stat")
    if proc.exists():
        try:  # the state comes after the command's name, which is in parentheses and can hold spaces
            return proc.read_text().rsplit(")", 1)[1].split()[0] == "Z"
        except (OSError, IndexError):
            return True  # the process ended between the two reads
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True, check=False).stdout.strip()
    return not state or state.startswith("Z")


@pytest.mark.real_clock("kills a child process")
def test_a_zombie_counts_as_stopped_and_a_running_process_does_not() -> None:
    """A child that this test kills and does not reap is a zombie, as a killed orphan is in a container."""
    child = subprocess.Popen(["sleep", "60"])
    try:
        assert not stopped(child.pid)
        os.kill(child.pid, signal.SIGKILL)
        deadline = time.monotonic() + 10
        while not stopped(child.pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert stopped(child.pid), "a killed child that nobody reaped"
    finally:
        child.kill()
        child.wait()


@pytest.mark.real_clock("waits for a fake command that hangs")
def test_a_command_past_the_limit_is_stopped_with_its_children(tmp_path: Path, repo: Path) -> None:
    """The fake shell starts a child, writes its PID and waits; the stop kills the whole group, child included.

    The child gets no part of the command's pipes, so a stop that kills only the shell still returns at once and the poll
    fails. Under load the 1 s stop can come before the shell wrote the PID: that try has no child to check, so the test tries
    again (up to 20 times) until a child started. Then it waits for a state (the child's process is gone), not for a time.
    """
    pid_file = tmp_path / "child.pid"
    command = f"sleep 60 >/dev/null 2>&1 & echo $! > {shlex.quote(str(pid_file))}; wait"
    for _ in range(20):
        assert run(tmp_path, repo, command, timeout=1) == ["Local review: did not run (no reply after 1 seconds)"]
        if pid_file.exists() and pid_file.read_text().strip().isdigit():
            break
        pid_file.unlink(missing_ok=True)
    else:
        pytest.fail("no try started the child before its stop")
    child = int(pid_file.read_text())
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if stopped(child):
            return
        time.sleep(0.05)
    os.kill(child, signal.SIGKILL)
    pytest.fail("the child outlived the stop")


@pytest.mark.real_clock("runs git as a subprocess")
def test_no_diff_and_a_missing_base_do_not_run(tmp_path: Path, repo: Path) -> None:
    command = fake(tmp_path, reply_body({"findings": [], "checked": []}))
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    assert run(tmp_path, repo, command) == ["Local review: did not run (no diff against origin/main)"]
    git(repo, "update-ref", "-d", "refs/remotes/origin/main")
    assert run(tmp_path, repo, command)[0].startswith("Local review: did not run (git diff failed: ")
    assert not (tmp_path / "prompt.txt").exists(), "the command never ran"


def test_a_missing_issue_file_does_not_run(tmp_path: Path) -> None:
    environ = {local_review.COMMAND_VARIABLE: "true"}
    with pytest.raises(local_review.DidNotRun, match=r"cannot read missing\.md"):
        local_review.review(tmp_path / "missing.md", None, "origin/main", tmp_path, environ)


@pytest.mark.real_clock("runs the script as a subprocess")
def test_the_script_exits_0_and_prints_the_line_when_it_does_not_run(tmp_path: Path, repo: Path) -> None:
    issue = tmp_path / "issue.md"
    issue.write_text("x\n")
    done = subprocess.run([sys.executable, str(REPO / ".github" / "local_review.py"), "--issue-file", str(issue)],
                          cwd=repo, capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"}, check=False)
    assert done.returncode == 0
    assert done.stdout == "Local review: did not run (no LOCAL_REVIEW_COMMAND set)\n"


def test_the_schema_is_the_issues() -> None:
    """The schema of the issue's Agent notes, as the command gets it."""
    finding = local_review.SCHEMA["properties"]["findings"]["items"]
    assert local_review.SCHEMA["required"] == ["findings", "checked"]
    assert finding["required"] == list(local_review.FINDING_KEYS)
    assert finding["properties"]["confidence"]["enum"] == ["high", "medium"]
    assert local_review.TIMEOUT_SECONDS == 600


def test_the_script_parses_as_python_3_9() -> None:
    """A run calls it with the Mac's `python3`, which can be /usr/bin/python3 (3.9): keep its syntax that old."""
    import ast
    path = REPO / ".github" / "local_review.py"
    ast.parse(path.read_text(), str(path), feature_version=(3, 9))


def test_an_unexpected_error_still_prints_the_line_and_exits_0(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def broken(*args: object, **kwargs: object) -> list[str]:
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
    monkeypatch.setattr(local_review, "review", broken)
    assert local_review.main(["--issue-file", "x.md"]) == 0
    assert capsys.readouterr().out == "Local review: did not run (UnicodeDecodeError)\n"
