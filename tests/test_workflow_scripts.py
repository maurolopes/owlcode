"""The logic of the `run:` blocks of gates-retry.yml, race-hunt.yml, flakes.yml and the like lives in scripts under .github/. (The
product's own scripts are tested in tests/test_product_ci.py, which is not in the loop's list.)
Each script runs here with a fake `gh`, `curl` or `python3` first on the PATH, so a test shows each of
its branches with no network. The wiring tests (a workflow calls its script) read the workflow files.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
GITHUB = REPO / ".github"
WORKFLOWS = GITHUB / "workflows"
BASH = ["bash", "--noprofile", "--norc", "-eo", "pipefail"]


def fake(bin_dir: Path, name: str, body: str) -> None:
    """A program first on the PATH: it logs its arguments to $FAKE_LOG, then runs `body` (a shell script)."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / name).write_text(f'#!/bin/sh\necho "{name} $*" >> "$FAKE_LOG"\n{body}\n')
    (bin_dir / name).chmod(0o755)


def run_script(script: str, cwd: Path, bin_dir: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    base = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(cwd), "FAKE_LOG": str(cwd / "calls.log")}
    return subprocess.run([*BASH, str(GITHUB / script)], cwd=cwd, env=base | env, capture_output=True, text=True, check=False)


def calls(cwd: Path) -> list[str]:
    log = cwd / "calls.log"
    return log.read_text().splitlines() if log.exists() else []


# ---- gates-retry-run.sh

SETUP_FAILURE = {"jobs": [{"name": "python", "conclusion": "failure", "steps": [
    {"name": "Set up job", "conclusion": "success"}, {"name": "Run jdx/mise-action@" + "a" * 40, "conclusion": "failure"}]}]}
GATE_FAILURE = {"jobs": [{"name": "python", "conclusion": "failure", "steps": [
    {"name": "Set up job", "conclusion": "success"}, {"name": "Run uv sync --frozen", "conclusion": "failure"}]}]}
NO_JOB = {"total_count": 0, "jobs": []}

FAKE_RETRY_GH = """case "$1 $2" in
  "api repos/o/r/actions/runs/42/jobs?per_page=100") cat "$FAKE_JOBS" ;;
  "run download") [ -z "$FAKE_NO_SUITE" ] || exit 1 ;;
  "pr list") echo "$FAKE_PULLS" ;;
esac
exit 0"""


def retry(tmp_path: Path, jobs: dict, event: str, pulls: list[dict] | None = None, no_suite: bool = False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "jobs.src").write_text(json.dumps(jobs))
    fake(tmp_path / "bin", "gh", FAKE_RETRY_GH)
    env = {"GITHUB_REPOSITORY": "o/r", "RUN_ID": "42", "EVENT": event, "HEAD": "a" * 40, "BRANCH": "abc-1-x", "FAKE_JOBS": str(tmp_path / "jobs.src"),
           "FAKE_PULLS": json.dumps(pulls or []), "FAKE_NO_SUITE": "1" if no_suite else ""}
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "gates_retry.py").write_text((GITHUB / "gates_retry.py").read_text())
    return run_script("gates-retry-run.sh", tmp_path, tmp_path / "bin", env)


def test_a_setup_failure_reruns_the_failed_jobs_and_a_failed_gate_reruns_nothing(tmp_path: Path) -> None:
    done = retry(tmp_path / "setup", SETUP_FAILURE, "schedule")
    assert done.returncode == 0 and done.stdout.splitlines()[-1] == "rerun", done.stderr
    assert calls(tmp_path / "setup")[-1] == "gh run rerun 42 --failed -R o/r"
    kept = retry(tmp_path / "gate", GATE_FAILURE, "schedule")
    assert kept.stdout.splitlines()[-1].startswith("keep") and not any(c.startswith("gh run rerun") for c in calls(tmp_path / "gate"))


def test_a_run_with_no_job_reruns_whole(tmp_path: Path) -> None:
    """GitHub failed the run at startup, so there is no failed job for `--failed`."""
    done = retry(tmp_path, NO_JOB, "schedule")
    assert done.stdout.splitlines()[-1] == "rerun all" and calls(tmp_path)[-1] == "gh run rerun 42 -R o/r"


def test_a_pull_request_run_reruns_only_while_its_head_is_an_open_pull_requests_head(tmp_path: Path) -> None:
    """The pull request's open head comes from `gh pr list`; no match keeps the run."""
    current = retry(tmp_path / "current", SETUP_FAILURE, "pull_request", [{"headRefOid": "a" * 40}])
    assert current.stdout.splitlines()[-1] == "rerun"
    assert calls(tmp_path / "current")[2] == "gh pr list -R o/r --head abc-1-x --state open --json headRefOid,isCrossRepository"
    old = retry(tmp_path / "old", SETUP_FAILURE, "pull_request", [{"headRefOid": "b" * 40}])
    assert old.stdout.splitlines()[-1].startswith("keep: no open pull request has the head") \
        and not any(c.startswith("gh run rerun") for c in calls(tmp_path / "old"))
    fork = retry(tmp_path / "fork", SETUP_FAILURE, "pull_request", [{"headRefOid": "a" * 40, "isCrossRepository": True}])
    assert fork.stdout.splitlines()[-1].startswith("keep: no open pull request has the head") \
        and not any(c.startswith("gh run rerun") for c in calls(tmp_path / "fork")), "a fork's run is never rerun (OWL-53)"
    retry(tmp_path / "other", SETUP_FAILURE, "schedule")
    assert not any(c.startswith("gh pr list") for c in calls(tmp_path / "other")), "a run that is no pull request's lists none"


def test_a_missing_suite_file_does_not_stop_the_retry(tmp_path: Path) -> None:
    """The download of the browser suite's counts fails when the suite did not end; the rule then reads none."""
    done = retry(tmp_path, SETUP_FAILURE, "schedule", no_suite=True)
    assert done.returncode == 0 and "no counts of the browser suite" in done.stdout and done.stdout.splitlines()[-1] == "rerun"


# ---- flakes-file.sh

FAKE_FLAKES_GH = """case "$1 $2" in
  "api repos/o/r/actions/runs/42") echo "${FAKE_HEAD_REPO-o/r}" ;;
  "run download") [ -z "$FAKE_NO_FILE" ] || exit 1; echo '[]' > "$9/flakes.json" ;;
esac
exit 0"""


def file_flakes(tmp_path: Path, no_file: bool, team: str | None = None, head_repo: str = "o/r") -> subprocess.CompletedProcess[str]:
    fake(tmp_path / "bin", "gh", FAKE_FLAKES_GH)
    fake(tmp_path / "bin", "python3", "exit 0")
    env = {"GITHUB_REPOSITORY": "o/r", "RUN_ID": "42", "ATTEMPT": "2", "RUN": "https://x/runs/42", "HEAD": "a" * 40, "BRANCH": "main",
           "FAKE_NO_FILE": "1" if no_file else "", "FAKE_HEAD_REPO": head_repo,
           **({"LINEAR_TEAM": team} if team is not None else {})}
    return run_script("flakes-file.sh", tmp_path, tmp_path / "bin", env)


def test_the_file_of_flaky_tests_of_the_attempt_goes_to_the_script(tmp_path: Path) -> None:
    """The script holds no team: flakes.py finds the repository's row in AGENTS.md "The teams", or takes LINEAR_TEAM."""
    done = file_flakes(tmp_path, no_file=False)
    assert done.returncode == 0, done.stderr
    assert calls(tmp_path) == ["gh api repos/o/r/actions/runs/42 --jq .head_repository.full_name // \"\"",
                               "gh run download 42 -R o/r -n flakes-2 -D flakes",
                               f"python3 -I .github/flakes.py flakes/flakes.json --team  --repo o/r --run https://x/runs/42 --head {'a' * 40} --branch main"]
    (tmp_path / "calls.log").unlink()
    assert file_flakes(tmp_path, no_file=False, team="XYZ").returncode == 0
    assert calls(tmp_path)[2].startswith("python3 -I .github/flakes.py flakes/flakes.json --team XYZ --repo o/r --run ")


def test_a_run_with_no_file_of_flaky_tests_ends_with_no_error(tmp_path: Path) -> None:
    """The web job did not run the suite: nothing to file, and the step passes."""
    done = file_flakes(tmp_path, no_file=True)
    assert done.returncode == 0 and "no file of flaky tests" in done.stdout
    assert calls(tmp_path)[1:] == ["gh run download 42 -R o/r -n flakes-2 -D flakes"]


def test_a_run_of_a_fork_files_no_flaky_test(tmp_path: Path) -> None:
    """A fork's gates run holds the fork's file of flaky tests, and the issue that main's script would file starts an agent
    (OWL-53). A run whose head repository is another one, or unknown, downloads nothing and files nothing."""
    for case, repo in (("fork", "stranger/r"), ("deleted", "")):
        done = file_flakes(tmp_path / case, no_file=False, head_repo=repo)
        assert done.returncode == 0 and "::notice::" in done.stdout and "no flaky test is filed" in done.stdout, case
        assert len(calls(tmp_path / case)) == 1, f"{case}: only the read of the run's head repository"


# ---- the workflows call the scripts

def load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text())


def test_a_script_that_runs_beside_a_secret_or_a_write_token_runs_mains_copy() -> None:
    """The workflow `flakes` (the Linear key) and `gates-retry` (`actions: write`) check out the default branch before the
    step that calls the script. A product's own workflows (the cleanup of its caches): its own tests."""
    for name, job in (("flakes.yml", "file"), ("gates-retry.yml", "retry")):
        steps = load(name)["jobs"][job]["steps"]
        checkout = next(i for i, s in enumerate(steps) if s.get("uses", "").startswith("actions/checkout@"))
        call = next(i for i, s in enumerate(steps) if str(s.get("run", "")).startswith("bash .github/"))
        assert checkout < call and steps[checkout]["with"]["ref"] == "${{ github.event.repository.default_branch }}", (name, job)


