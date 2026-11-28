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
    assert len(steps) == 4
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
        assert run.replace('cat "$RUNNER_TEMP/err"', "").count("cat ") == 0 and "LEAK_PATTERNS_FILE" not in run


def test_the_job_runs_on_githubs_runner_for_at_most_10_minutes():
    """leak.yml is this repository's own (.workflow/loop-files.txt leaves it out), so tests/test_ci.py does not check it."""
    assert JOB["runs-on"] == "ubuntu-latest"
    assert JOB["timeout-minutes"] <= 10


def test_the_loop_files_list_leaves_leak_yml_out():
    lines = (REPO / ".workflow" / "loop-files.txt").read_text().splitlines()
    assert "!.github/workflows/leak.yml" in lines


def test_each_tracked_path_gets_to_the_guard_as_itself_and_a_line_with_no_file_fails():
    step = next(s for s in STEPS if s["name"] == "Every tracked file of the head")
    run = step["run"]
    assert "git -c core.quotePath=false ls-files" in run  # no quoted name
    assert "s/[][*?]/[&]/g" in run  # glob characters of a name are literal
    assert "s|^[#!]|./&|" in run  # a leading # or ! is no comment or exclusion
    assert 'grep -q "names no file"' in run and "exit $status" in run  # a line that names no file fails closed


def test_a_failed_git_ls_files_fails_the_step_and_the_guard_s_status_reaches_the_log():
    """OWL-93 (R2-1 of PR #21): GitHub's default `bash -e` has no pipefail, so a failed `git ls-files` gave an empty list and exit 0,
    and `status=$?` never ran after a non-zero exit. `shell: bash` sets pipefail, and `|| status=$?` keeps the guard's stderr."""
    for name in ("Every tracked file of the head", "Every tracked path and the branch's name"):
        assert next(s for s in STEPS if s["name"] == name)["shell"] == "bash"
    run = next(s for s in STEPS if s["name"] == "Every tracked file of the head")["run"]
    assert 'status=0; /usr/bin/python3' in run and '|| status=$?' in run and "; status=$?" not in run


def test_the_paths_and_the_branch_s_name_go_through_the_guard_as_text():
    """OWL-93 (R1-4 of PR #21): a file name or a branch name that names a product leaks as much as a file's contents."""
    step = next(s for s in STEPS if s["name"] == "Every tracked path and the branch's name")
    assert step["env"]["BRANCH"] == "${{ github.event.pull_request.head.ref }}", "by the environment, never by interpolation"
    assert "github.event" not in step["run"]
    assert "git -c core.quotePath=false ls-files" in step["run"] and 'printf \'%s\\n\' "$BRANCH"' in step["run"]
    assert "leak_guard.py stdin" in step["run"]
