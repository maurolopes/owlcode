"""The rule of .github/gates_retry.py and the wiring of the workflow gates-retry.yml."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("gates_retry", REPO / ".github" / "gates_retry.py")
assert spec and spec.loader
gates_retry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gates_retry)

MISE = "Run jdx/mise-action@2d8d4cafcbd33be2ea37d2b6f5ad595363d1f1ca"


def job(name: str, conclusion: str, failed: tuple[str, ...] = ()) -> dict[str, Any]:
    steps = [{"name": "Set up job", "conclusion": "success"}] + [{"name": n, "conclusion": "failure"} for n in failed]
    return {"name": name, "conclusion": conclusion, "steps": steps}


def test_a_job_that_failed_in_a_setup_step_is_rerun_and_a_failed_gate_is_not():
    """The run of 2026-11-08 failed at the mise step: rerun. A failed test, an `exit 1` guard, `uv sync` and a
    job with no failed step stay red."""
    decide = gates_retry.decide
    assert decide({"jobs": [job("changes", "success"), job("python", "failure", (MISE,))]}) == "rerun"
    assert decide({"jobs": [job("python", "failure", ("Run actions/checkout@" + "a" * 40,)), job("web", "failure", ("ghostscript and pdftotext, as deploy/install.sh installs them",))]}) == "rerun"
    assert decide({"jobs": [job("python", "failure", (MISE,)), job("web", "failure", ("Run npm run lint",))]}).startswith("keep"), \
        "one failed gate in the run keeps the whole run as it is"
    for gate in ("pytest (a skip fails here: every tool is installed; tests/conftest.py)", "Run uv sync --frozen", "Run npm ci",
                 "No agent attribution line (the job `changes` names each one it found; AGENTS.md Rules)",
                 "Unit tests and the browser suite (the runner's Google Chrome; the suite builds the app first and drives the build)"):
        assert decide({"jobs": [job("python", "failure", (gate,))]}).startswith("keep"), gate
    assert decide({"jobs": [job("python", "failure")]}).startswith("keep"), "no failed step: a lost or cancelled runner"
    assert decide({"jobs": [job("python", "success")]}) == "keep: no failed job"
    assert decide({}) == "keep: no failed job"


SUITE = "Unit tests and the browser suite (the runner's Google Chrome; the suite builds the app first and drives the build)"
NOT_RUN = {"name": "a saved item stays first", "location": "lambda.spec.mjs:295"}


def counts(**more: Any) -> dict[str, Any]:
    """The suite's counts (web/.screenshots/run.json) of a run in which every test passed."""
    return {"status": "failed", "total": 694, "unexpected": [], "interrupted": 0, "flaky": 0, "didNotRun": [],
            "errors": [], "checks": {"whole": True, "missing": [], "new": []}} | more


def test_a_web_job_that_failed_with_no_test_failed_after_its_retry_is_rerun_once():
    """The owner's call 2026-11-16: a web job had 694 passed, 1 did not run and one error
    outside any test (`route.fetch: Test ended`); its checks reporter named the check of that test MISSING."""
    decide = gates_retry.decide
    web = {"jobs": [job("python", "success"), job("web", "failure", (SUITE,))]}
    pr_838 = counts(didNotRun=[NOT_RUN], errors=["route.fetch: Test ended."],
                    checks={"whole": True, "missing": ["Page: a saved item stays first"], "new": [],
                            "inTestsNotRun": ["Page: a saved item stays first"]})
    assert decide(web, pr_838) == "rerun", "a did-not-run-only run"
    assert decide(web, counts(flaky=1)) == "rerun", "a flaky-only run (failOnFlakyTests)"
    assert decide(web, counts(errors=["Error: worker crashed"])) == "rerun", "an error outside a test alone"
    for why, run in (("a real failure", counts(flaky=1, unexpected=[{"location": "x.spec.mjs:1"}])),
                     ("no counts file", None), ("a counts file that is not an object", []),
                     ("a check not listed", counts(flaky=1, checks={"whole": True, "missing": [], "new": ["a new check"]})),
                     ("a check that stopped running, with every test run", counts(flaky=1, checks={"missing": ["x"], "new": []})),
                     ("a run.json written before its fields", counts(didNotRun=[NOT_RUN], checks={"missing": ["x"], "new": []})),
                     ("no checks reporter result", counts(flaky=1, checks=None)),
                     ("an interrupted test", counts(flaky=1, interrupted=1)),
                     ("the global time-out", counts(flaky=1, status="timedout")),
                     ("no test ran: a build that failed", counts(total=0, errors=["build failed"])),
                     ("every test did not run", counts(total=1, didNotRun=[NOT_RUN])),
                     ("nothing flaky, nothing missing", counts())):
        assert decide(web, run).startswith("keep: job web failed in the browser suite"), why
    assert decide({"jobs": [job("python", "failure", (SUITE,))]}, counts(flaky=1)).startswith("keep"), \
        "only the web job's browser suite"
    assert decide({"jobs": [job("web", "failure", (SUITE, "Run npm run lint"))]}, counts(flaky=1)).startswith("keep"), \
        "a gate beside it stays red"
    assert decide({"jobs": [job("web", "failure", (SUITE,)), job("python", "failure", (MISE,))]}, counts(flaky=1)) == "rerun"


def test_a_test_that_did_not_run_takes_all_its_checks_and_a_check_of_a_test_that_ran_keeps():
    """Lambda.spec.mjs:314 holds three checks that no other test has. When it does not run, its three names
    are MISSING, and the job is rerun. A MISSING name that no test that did not run holds stopped running in a test that
    ran: the job stays red, however many tests did not run."""
    decide = gates_retry.decide
    web = {"jobs": [job("web", "failure", (SUITE,))]}
    three = ["Page: the first panel is drawn", "Page: the items that fit are in",
             "Page: the first panel's shortcut opens nothing that the page does not draw"]
    not_run = [{"name": "the first panel's shortcuts open nothing that the page does not draw", "location": "lambda.spec.mjs:314"}]
    run = counts(didNotRun=not_run, checks={"whole": True, "missing": three, "new": [], "inTestsNotRun": three})
    assert decide(web, run) == "rerun"
    ran = counts(didNotRun=not_run * 5, checks={"whole": True, "missing": [*three, "Page: a check of a test that ran"], "new": [],
                                                "inTestsNotRun": three})
    assert decide(web, ran) == ("keep: job web failed in the browser suite: a check stopped running that no test that did not"
                                " run holds: Page: a check of a test that ran")


def test_the_suite_file_reaches_the_rule_and_a_missing_one_keeps(tmp_path, capsys):
    jobs = tmp_path / "jobs.json"
    jobs.write_text(json.dumps({"jobs": [job("web", "failure", (SUITE,))]}))
    suite = tmp_path / "run.json"
    pulls = tmp_path / "pulls.json"
    pulls.write_text(json.dumps([{"headRefOid": "a" * 40}]))
    suite.write_text(json.dumps(counts(flaky=1)))
    assert gates_retry.main(["gates_retry.py", str(jobs), "--pulls", str(pulls), "--head", "a" * 40, "--suite", str(suite)]) == 0
    assert capsys.readouterr().out == "rerun\n"
    gates_retry.main(["gates_retry.py", str(jobs), "--suite", str(tmp_path / "none.json")])
    assert "no counts of the browser suite" in capsys.readouterr().out
    suite.write_text("not json")
    gates_retry.main(["gates_retry.py", str(jobs), "--suite", str(suite)])
    assert capsys.readouterr().out.startswith("keep")


def test_the_suite_job_and_step_come_from_the_environment(tmp_path):
    """GATES_SUITE_JOB and GATES_SUITE_STEP name another product's browser suite; with neither, the job is `web` and the
    step is gates.yml's. The values are read when the script starts, so the script runs in a new process."""
    jobs = tmp_path / "jobs.json"
    suite = tmp_path / "run.json"
    suite.write_text(json.dumps(counts(flaky=1)))

    def answer(failed: dict[str, Any], **environ: str) -> str:
        jobs.write_text(json.dumps({"jobs": [failed]}))
        done = subprocess.run([sys.executable, "-I", str(REPO / ".github" / "gates_retry.py"), str(jobs), "--suite", str(suite)],
                              capture_output=True, text=True, check=True, env={**{k: v for k, v in os.environ.items() if not k.startswith("GATES_SUITE_")}, **environ})
        return done.stdout.strip()

    other = {"GATES_SUITE_JOB": "e2e", "GATES_SUITE_STEP": "Playwright"}
    assert answer(job("e2e", "failure", ("Playwright (the suite)",)), **other) == "rerun"
    assert answer(job("web", "failure", (SUITE,)), **other).startswith("keep"), "the default job is no suite's job now"
    assert answer(job("web", "failure", (SUITE,))) == "rerun"
    assert answer(job("e2e", "failure", ("Playwright (the suite)",))).startswith("keep")


def test_a_run_that_github_failed_at_startup_runs_again_whole():
    """Run 42672208017 (2026-11-09) completed as a failure with zero jobs, and a rerun started normally. It has no
    failed job for `--failed`, so the whole run runs again."""
    assert gates_retry.decide({"total_count": 0, "jobs": []}) == "rerun all"


def test_the_workflow_reruns_failed_jobs_once_with_its_own_token_from_main():
    path = REPO / ".github" / "workflows" / "gates-retry.yml"
    flow = yaml.safe_load(path.read_text())
    on = flow.get(True, flow.get("on"))
    assert on == {"workflow_run": {"workflows": ["gates"], "types": ["completed"]}}
    retry = flow["jobs"]["retry"]
    assert retry["if"] == "github.event.workflow_run.conclusion == 'failure' && github.event.workflow_run.run_attempt == 1", \
        "a failed run, the first attempt only: a second failure is the owner's"
    assert retry["permissions"] == {"actions": "write", "contents": "read", "pull-requests": "read"} and flow["permissions"] == {"contents": "read"}
    text = path.read_text()
    assert "secrets." not in text and "create-github-app-token" not in text, "its own token, no app key"
    checkout = next(s for s in retry["steps"] if s.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["ref"] == "${{ github.event.repository.default_branch }}", "main's script, never a branch's"
    assert [s["run"] for s in retry["steps"] if "run" in s] == ["bash .github/gates-retry-run.sh"], "One call of the script"
    run = (REPO / ".github" / "gates-retry-run.sh").read_text()
    assert 'python3 .github/gates_retry.py jobs.json --pulls pulls.json --head "$HEAD" --suite suite/run.json' in run
    assert '"$EVENT" = pull_request' in run and "-n flakes-1 -D suite ||" in run, "the counts of attempt 1; none keeps the job red"
    assert retry["env"]["HEAD"] == "${{ github.event.workflow_run.head_sha }}"
    assert 'python3 .github/gates_retry.py jobs.json' in run and 'gh run rerun "$RUN_ID" --failed' in run and '"$answer" = rerun' in run
    assert '[ "$answer" = "rerun all" ]; then\n  gh run rerun "$RUN_ID" -R' in run, "no job: the whole run"
    assert (REPO / ".github" / "gates_retry.py").read_text().count("Standard library only") == 1
    json.dumps(on)  # the trigger is plain data


def test_a_pull_request_run_is_rerun_only_while_its_head_is_current(tmp_path, capsys):
    """A rerun keeps the run's concurrency group, so the rerun of an outdated head would cancel the new head's gates."""
    jobs = tmp_path / "jobs.json"
    jobs.write_text(json.dumps({"jobs": [job("python", "failure", (MISE,))]}))
    pulls = tmp_path / "pulls.json"
    for open_heads, expected in ((["a" * 40], "rerun"), (["b" * 40], "keep"), ([], "keep")):
        pulls.write_text(json.dumps([{"headRefOid": h} for h in open_heads]))
        assert gates_retry.main(["gates_retry.py", str(jobs), "--pulls", str(pulls), "--head", "a" * 40]) == 0
        assert capsys.readouterr().out.startswith(expected), open_heads
    jobs.write_text(json.dumps({"jobs": [job("python", "failure", ("Run npm ci",))]}))
    pulls.write_text(json.dumps([{"headRefOid": "a" * 40}]))
    gates_retry.main(["gates_retry.py", str(jobs), "--pulls", str(pulls), "--head", "a" * 40])
    assert capsys.readouterr().out.startswith("keep: job python failed"), "a failed gate stays red on a current head too"
    jobs.write_text(json.dumps({"total_count": 0, "jobs": []}))
    for open_heads, expected in ((["a" * 40], "rerun all\n"), (["b" * 40], "keep: no open pull request")):
        pulls.write_text(json.dumps([{"headRefOid": h} for h in open_heads]))
        gates_retry.main(["gates_retry.py", str(jobs), "--pulls", str(pulls), "--head", "a" * 40])
        assert capsys.readouterr().out.startswith(expected), "a run with no job keeps the rule of the current head too"
    assert gates_retry.main(["gates_retry.py", str(jobs), "--pulls", str(pulls)]) == 2
