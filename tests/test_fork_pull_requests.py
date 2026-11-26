"""A fork's pull request on a public repository (OWL-53; agents/README.md "Fork pull requests").

Anyone can open a pull request from a fork. The workflows that run on `pull_request_target` and on `workflow_run` hold the
repository's secrets, so each of them has a property that these tests pin:

- no step that holds a secret or a write token runs, sources or reads as code a file of the pull request's head;
- no value of the pull request (title, body, branch name, comment) goes into a shell line: it goes through `env:`;
- a fork's pull request gets no review, no rerun, no issue and no update: each script that reads the open pull requests,
  or a run's head, leaves a fork's out.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml

from tests.test_ci import OPEN_STEP, run_step

REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO / ".github" / "workflows"
# the seven workflows that run with the secrets when a fork's pull request starts them (OWL-53)
SEVEN = ("attribution", "apply-workflow-patch", "branch-update", "flakes", "gates-failed", "gates-retry", "verdict-rerun")
# the one checkout of a pull request's branch: apply-workflow-patch's `git apply` of the owner's patch, into `branch/`, as data
BRANCH_CHECKOUT = ("apply-workflow-patch", "branch")
CHECKOUT = "actions/checkout@"


def flow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / f"{name}.yml").read_text())


def steps_of(name: str) -> list[tuple[str, dict]]:
    return [(job, step) for job, body in flow(name)["jobs"].items() for step in body.get("steps") or []]


def load(script: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"fork_{script}", REPO / ".github" / f"{script}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---- the workflows' property

@pytest.mark.parametrize("name", SEVEN)
def test_no_value_of_the_pull_request_goes_into_a_shell_line(name: str) -> None:
    """A `${{ }}` in a `run:` block is text that the shell runs: a branch name or a title with a quote, a backtick or `$(...)`
    would run as code beside the secrets. The workflow puts each value into `env:` and the script reads the variable."""
    for job, step in steps_of(name):
        if "run" in step:
            assert "${{" not in str(step["run"]), f"{name}, job {job}: a run block holds an expression: use env:"


@pytest.mark.parametrize("name", SEVEN)
def test_no_job_checks_out_the_pull_requests_head_as_code(name: str) -> None:
    """A checkout runs in a job with the repository's secrets, so it takes main's commit (the default branch), or the base
    commit of `pull_request_target` (`github.sha`). The one exception is the owner's patch, applied to the branch as data."""
    for job, step in steps_of(name):
        uses = str(step.get("uses", ""))
        assert not uses.startswith("./"), f"{name}, job {job}: a local action is a file of the checkout, so of the pull request"
        if not uses.startswith(CHECKOUT):
            continue
        with_ = step.get("with") or {}
        ref = " ".join(str(with_.get("ref", "")).split())
        if (name, with_.get("path")) == BRANCH_CHECKOUT:
            assert ref == "${{ github.event.pull_request.head.ref }}", f"{name}: the patch goes onto the pull request's branch"
            continue
        assert ref in ("${{ github.event.repository.default_branch }}", "${{ github.sha }}"), \
            f"{name}, job {job}: the checkout's ref {ref!r} is not main's"
        if ref == "${{ github.sha }}":
            assert "pull_request_target" in flow(name)[True], f"{name}: `github.sha` is main's only on pull_request_target"


def test_the_branch_checkout_of_the_patch_is_data_that_no_step_runs() -> None:
    """`branch/` holds the pull request's files. The only step that works in it calls main's script (`../main/`), and no other
    step names the folder: nothing reads a file of it as code."""
    steps = steps_of("apply-workflow-patch")
    branch = [s for _, s in steps if s.get("working-directory") == "branch"]
    assert len(branch) == 1 and str(branch[0]["run"]).startswith("python3 ../main/.github/apply_workflow_patch.py ")
    main_checkout = [s for _, s in steps if str(s.get("uses", "")).startswith(CHECKOUT) and s["with"].get("path") == "main"]
    assert len(main_checkout) == 1 and "default_branch" in main_checkout[0]["with"]["ref"], "the script is main's"
    for _, step in steps:
        assert "branch/" not in str(step.get("run", "")) and "./branch" not in str(step.get("run", ""))


def test_the_patch_job_never_runs_for_a_fork() -> None:
    """The owner's label on a fork's pull request would put the push token beside the fork's branch: the job's `if` needs the
    head to be in this repository, and the owner as the sender."""
    condition = " ".join(str(flow("apply-workflow-patch")["jobs"]["apply"]["if"]).split())
    assert "github.event.pull_request.head.repo.full_name == github.repository" in condition
    assert "github.event.sender.login == github.repository_owner" in condition


def test_the_workflows_that_run_main_s_copy_never_run_on_the_pull_request_event() -> None:
    """`pull_request_target` and `workflow_run` run main's copy of the file with the secrets. These seven workflows
    use them, and none of them is triggered by `pull_request` (a fork's own event, which has no secret and runs the fork's file)."""
    for name in SEVEN:
        events = flow(name)[True]
        assert "pull_request" not in events, name
        assert "pull_request_target" in events or "workflow_run" in events, name


# ---- the review's start

PAYLOAD = {"pull_request": {"head": {"repo": {"full_name": "owner/repo"}}}}
FORK_PAYLOAD = {"pull_request": {"head": {"repo": {"full_name": "stranger/repo"}}}}
DELETED_FORK_PAYLOAD = {"pull_request": {"head": {"repo": None}}}
KEY = "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----"


def access(tmp_path: Path, payload: dict | None) -> tuple[subprocess.CompletedProcess[str], str]:
    tmp_path.mkdir(parents=True)
    out, event = tmp_path / "out", tmp_path / "event.json"
    out.write_text("")
    env = {"PATH": "/usr/bin:/bin", "APP_ID": "Iv23client", "APP_KEY": KEY, "GITHUB_OUTPUT": str(out),
           "GITHUB_REPOSITORY": "owner/repo"}
    if payload is not None:
        event.write_text(json.dumps(payload))
        env["GITHUB_EVENT_PATH"] = str(event)
    run = subprocess.run([sys.executable, "-I", str(REPO / ".github" / "review_jobs.py"), "access"], env=env,
                         capture_output=True, text=True, check=False)
    return run, out.read_text()


def test_the_review_does_not_start_for_a_fork_s_pull_request_event(tmp_path: Path) -> None:
    """The event's payload says where the head is. A fork gets a notice and `ready=false`, so the steps that need the
    reviewer app's token do not run and the job `review` (the paid runner) never starts. A head in this repository, an
    event with no pull request (a run by hand) and an event with no payload go on."""
    for case, payload in (("fork", FORK_PAYLOAD), ("deleted", DELETED_FORK_PAYLOAD)):
        run, out = access(tmp_path / case, payload)
        assert run.returncode == 0 and out == "ready=false\n", case
        assert "::notice::" in run.stdout and "a fork" in run.stdout and "agents/README.md" in run.stdout, case
    for case, payload in (("own", PAYLOAD), ("dispatch", {"inputs": {"pr": "7"}}), ("none", None)):
        run, out = access(tmp_path / case, payload)
        assert run.returncode == 0 and out == "ready=true\n", (case, run.stdout)


def test_the_review_does_not_start_for_a_fork_s_pull_request_that_a_run_by_hand_names(tmp_path: Path) -> None:
    """A run by hand (`workflow_dispatch`) has the secrets and no payload for the pull request: the step `open` asks GitHub
    where the head is. A fork gets a notice, the `head` output and no check, so `review` and `report` skip."""
    def start(case: str, cross: bool) -> tuple[subprocess.CompletedProcess[str], list[str], str]:
        out = tmp_path / f"{case}.out"
        view = json.dumps({"headRefOid": "abc123", "headRefName": "abc-1-x", "title": "ABC-1: x", "isDraft": False,
                           "isCrossRepository": cross})
        run, calls = run_step(tmp_path / case, "start", OPEN_STEP, [("pr view 7", view, 0), ("check_name=verdict", "[]", 0), ("check-runs", "99", 0)],
                              more={"GITHUB_OUTPUT": str(out), "LABELED_HEAD": ""})
        return run, calls, out.read_text()

    run, calls, out = start("fork", cross=True)
    assert run.returncode == 0 and "::notice::" in run.stdout and "a fork" in run.stdout
    assert out == "head=abc123\n", "no `check` output: the jobs review and report skip"
    assert not any("check-runs" in c for c in calls), "no check is written for a fork's head"
    assert any("isCrossRepository" in c for c in calls)
    run, calls, out = start("own", cross=False)
    assert run.returncode == 0 and "check=99" in out and any("status=in_progress" in c for c in calls)


# ---- the scripts that read the open pull requests

def reply(*pulls: dict) -> dict:
    return {"data": {"repository": {"pullRequests": {"nodes": list(pulls)}}}}


def test_the_watchdog_never_reads_a_fork_s_pull_request() -> None:
    """A fork's ready pull request with no verdict would get its review dispatched by hand, which has the secrets, and on the
    paid runner. The watchdog's list leaves it out, so it dispatches, comments and reruns nothing for it."""
    from tests.test_review_watchdog import BOT, NOW, OWNER, pull, watchdog

    assert "isCrossRepository" in watchdog.QUERY
    own, fork = pull(7), pull(8) | {"isCrossRepository": True}
    pulls = watchdog.pull_requests(reply(own | {"isCrossRepository": False}, fork))
    assert [p["number"] for p in pulls] == [7]
    assert [(a["pr"], a["action"]) for a in watchdog.choose(pulls, [], NOW, OWNER, BOT)] == [(7, "dispatch")]
    assert watchdog.stalled_gates(pulls, [{"id": 1, "head_sha": fork["headRefOid"], "event": "pull_request", "status": "completed",
                                           "conclusion": "failure", "run_attempt": 1, "updated_at": "2026-11-05T10:00:00Z"}],
                                  NOW) == [], "a fork's gates run at startup is not rerun by the watchdog"


def test_the_branch_update_never_updates_a_fork_s_pull_request() -> None:
    from tests.test_branch_update import pull, update

    assert "isCrossRepository" in update.QUERY
    fork = pull(3) | {"isCrossRepository": True}
    assert update.choose(reply(fork)) == "", "a conflict on a fork's pull request is the fork's to fix"
    assert update.choose(reply(fork, pull(9))) == f"update 9 {9:040d}"
    owner_fork = pull(4, auto=None, labels=("escalated",)) | {"isCrossRepository": True}
    assert update.choose(reply(owner_fork)) == ""


def test_the_gates_failed_comment_never_goes_to_a_fork_s_pull_request() -> None:
    from tests.test_gates_failed import gates, pull

    assert "isCrossRepository" in gates.QUERY
    fork = pull(8) | {"isCrossRepository": True}
    sent = gates.to_send(reply(pull(7), fork), ["python"])
    assert [p["number"] for p, _ in sent] == [7]


def test_a_rerun_of_the_gates_never_goes_to_a_fork_s_run() -> None:
    retry = load("gates_retry")
    head = "a" * 40
    assert retry.current([{"headRefOid": head}], head)
    assert retry.current([{"headRefOid": head, "isCrossRepository": False}], head)
    assert not retry.current([{"headRefOid": head, "isCrossRepository": True}], head)
    assert retry.current([{"headRefOid": head, "isCrossRepository": True}, {"headRefOid": head}], head), "an own pull request counts"


def test_the_rerun_check_looks_only_at_a_pull_request_of_this_repository(tmp_path: Path) -> None:
    """The check `verdict` and the auto-merge of a passed rerun go to the pull request that `gh pr list --head BRANCH` finds
    by the branch's name: a fork's pull request has any name, so the list leaves out a head in another repository."""
    from tests.test_ci import RERUN_CHECK, RERUN, run_step as step

    output = tmp_path / "out"
    output.write_text("")
    env = {"BRANCH": "abc-9-x", "HEAD": "a" * 40, "REVIEWER": "r[bot]", "UPDATER": "u[bot]", "GITHUB_OUTPUT": str(output)}
    _, calls = step(tmp_path, "rerun", RERUN_CHECK, [("pr list", "", 0)], more=env, flow=RERUN)
    [listing] = [c for c in calls if c.startswith("pr list")]
    assert "isCrossRepository" in listing and "(.isCrossRepository | not)" in listing


# ---- the document

def test_the_guards_section_says_what_a_fork_s_pull_request_gets() -> None:
    """agents/README.md "The guards" has a section on fork pull requests: what runs, with what token, and what the owner sets
    on GitHub (OWL-53, Done when 4)."""
    text = (REPO / "agents" / "README.md").read_text()
    guards = text[text.index("\n## The guards"):text.index("\n## What can start now")]
    assert re.search(r"^### Fork pull requests$", guards, re.M), "a section of its own, inside The guards"
    section = guards[guards.index("### Fork pull requests"):]
    section = section[:section.index("\n### ", 5)] if "\n### " in section[5:] else section
    for word in ("no secret", "read-only", "LOOP_REVIEW_RUNNER", "Require approval", "outside collaborators", "isCrossRepository",
                 "only guard of the paid runner", "`runs-on:`", "main's copy only"):
        assert word in " ".join(section.split()), f"the section names {word!r}"
