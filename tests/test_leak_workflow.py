"""The workflow leak.yml (OWL-85): the leak check runs main's code with the owner's patterns, and fails closed."""

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
TEXT = (REPO / ".github" / "workflows" / "leak.yml").read_text()
JOB = yaml.safe_load(TEXT)["jobs"]["leak"]
STEPS = JOB["steps"]
FAIL_CLOSED = "the secret LEAK_PATTERNS is not set: the leak check cannot run"


def guard_steps() -> list[dict]:
    return [s for s in STEPS if "leak_guard.py" in s.get("run", "")]


def test_it_runs_on_the_events_that_hold_the_title_and_the_body():
    on = yaml.safe_load(TEXT)[True]  # YAML reads the key `on` as True
    assert set(on["pull_request"]["types"]) == {"opened", "synchronize", "reopened", "edited", "ready_for_review"}


def test_the_guard_is_main_s_copy_and_the_head_is_data_only():
    checkouts = [s for s in STEPS if str(s.get("uses", "")).startswith("actions/checkout")]
    by_path = {s["with"]["path"]: s["with"] for s in checkouts}
    assert by_path["main"]["ref"] == "${{ github.event.repository.default_branch }}"
    assert by_path["head"]["ref"] == "${{ github.event.pull_request.head.sha }}"
    assert all(s["with"]["persist-credentials"] is False for s in checkouts)
    steps = guard_steps()
    assert len(steps) == 3
    for step in steps:
        assert re.search(r"/usr/bin/python3 -I (\.\./)?main/\.github/leak_guard\.py", step["run"])
        assert not re.search(r"(?<!/bin/)python3? ", step["run"])  # no interpreter from PATH
        assert "head/" not in step["run"].replace("cd head", "")  # nothing of the head runs


def test_the_secret_is_in_one_step_and_the_guard_steps_get_only_the_file_path():
    holders = [s for s in STEPS if "secrets.LEAK_PATTERNS" in str(s)]
    assert len(holders) == 1 and "leak_guard.py" not in holders[0]["run"]
    assert TEXT.count("secrets.") == 1
    for step in guard_steps():
        assert step["env"]["LEAK_PATTERNS_FILE"] == "${{ runner.temp }}/leak-patterns.txt"
        assert "LEAK_PATTERNS" not in {k for k in step["env"] if k != "LEAK_PATTERNS_FILE"}
    assert "umask 077" in holders[0]["run"]
    assert "GITHUB_ENV" not in TEXT


def test_with_no_secret_the_job_fails_with_one_line_before_any_check():
    first = STEPS[0]
    assert first["env"]["LEAK_PATTERNS"] == "${{ secrets.LEAK_PATTERNS }}"
    assert f'[ -z "$LEAK_PATTERNS" ] && {{ echo "{FAIL_CLOSED}"; exit 1; }}' in first["run"]
    assert "if" not in JOB and "continue-on-error" not in JOB
    assert not any("if" in s or "continue-on-error" in s for s in STEPS)
    assert first["run"].index("exit 1") < first["run"].index("printf")


def test_the_title_and_the_body_come_by_the_environment_never_by_interpolation_into_the_shell():
    step = next(s for s in STEPS if s["name"] == "The pull request's title and body")
    assert "github.event" not in step["run"]
    assert step["env"]["TITLE"] == "${{ github.event.pull_request.title }}"
    assert step["env"]["BODY"] == "${{ github.event.pull_request.body }}"


def test_no_step_prints_a_pattern_or_a_matched_word():
    for step in guard_steps():
        run = step["run"]
        assert "--counts-only" in run or "tail -n 1" in run  # the match lines never reach the log
        assert "cat " not in run and "LEAK_PATTERNS_FILE" not in run
