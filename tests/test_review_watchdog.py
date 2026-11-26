"""The review watchdog (.github/review_watchdog.py and .github/workflows/review-watchdog.yml): a ready pull request
whose head has no check `verdict` 15 minutes after its ready mark or push gets its review started again once, or a comment
that says what it waits for. The step runs with gh faked, never the network."""

import importlib.util
import json
import subprocess
import sys
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from types import ModuleType

import yaml

from tests.conftest import REAL_REPO
from tests.test_ci import LOGGING_GH

WATCHDOG = REAL_REPO / ".github" / "workflows" / "review-watchdog.yml"
STEP = "Start a lost review again, or say what the pull request waits for"
GATES_STEP = "Rerun the gates that GitHub failed at startup"
# the command that each step runs (the logic is in review_watchdog.py, and the workflow only calls it)
COMMANDS = {STEP: "python3 -I .github/review_watchdog.py watch", GATES_STEP: "python3 -I .github/review_watchdog.py rerun-gates"}
OWNER = "maurolopes"
BOT = "maurolopes-reviewer"  # GraphQL leaves out the [bot] of REST's login
NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
OLD = "2026-11-05T11:00:00Z"  # an hour before NOW: past the 15 minutes
FRESH = "2026-11-05T11:50:00Z"  # 10 minutes before NOW


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("github_review_watchdog", REAL_REPO / ".github" / "review_watchdog.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


watchdog = load()


def pull(number: int, *, draft: bool = False, verdict: bool | str = False, pushed: str = OLD, ready: tuple[str, ...] = (),
         labels: tuple[str, ...] = (), reviews: tuple[dict, ...] = (), comments: tuple[dict, ...] = (),
         opened: str = "2026-11-05T09:00:00Z") -> dict:
    """One pull request as the query reads it; its head is `{number:040d}`, pushed when its first check suite began.
    `verdict`: True for a check `verdict`, or its status (IN_PROGRESS, COMPLETED)."""
    check = {"name": "verdict"} | ({"status": verdict} if isinstance(verdict, str) else {})
    suites = [{"createdAt": pushed, "checkRuns": {"nodes": [check] if verdict else []}}]
    return {"number": number, "isDraft": draft, "createdAt": opened, "headRefOid": f"{number:040d}",
            "labels": {"nodes": [{"name": n} for n in labels]}, "reviews": {"nodes": list(reviews)},
            "comments": {"nodes": list(comments)},
            "timelineItems": {"nodes": [{"createdAt": t} for t in ready]},
            "headRef": {"target": {"oid": f"{number:040d}", "committedDate": "2026-11-05T08:00:00Z",
                                   "checkSuites": {"nodes": suites}}}}


def approve(number: int, login: str = OWNER, head: str | None = None) -> dict:
    return {"author": {"login": login}, "state": "APPROVED", "commit": {"oid": head or f"{number:040d}"}}


def said(number: int, action: str, when: str = OLD, login: str = BOT, head: str | None = None) -> dict:
    marker = watchdog.MARKER.format(action=action, head=head or f"{number:040d}")
    return {"author": {"login": login}, "body": f"Some words.\n{marker}", "createdAt": when}


def run(number: int | None = None, status: str = "in_progress", title: str = "a title", event: str = "pull_request",
        run_id: int = 1, conclusion: str | None = None, attempt: int = 1) -> dict:
    return {"id": run_id, "status": status, "conclusion": conclusion, "run_attempt": attempt, "display_title": title,
            "event": event, "pull_requests": [{"number": number}] if number is not None else []}


def verdict_review(number: int, state: str = "APPROVED", when: str = "2026-11-05T11:45:00Z", head: str | None = None) -> dict:
    """The reviewer app's review of the head, as the query reads it (GraphQL: no [bot])."""
    return {"author": {"login": BOT}, "state": state, "submittedAt": when, "commit": {"oid": head or f"{number:040d}"}}


def jobs(*failed: str) -> dict:
    """The jobs API's reply for a review run: each job named in `failed` failed, the others passed."""
    return {"total_count": 4, "jobs": [{"name": n, "conclusion": "failure" if n in failed else "success"}
                                       for n in ("start", "carry", "review", "report")]}


def choose(*pulls: dict, runs: tuple[dict, ...] = (), jobs: dict | None = None) -> list[tuple[int, str]]:
    return [(a["pr"], a["action"]) for a in watchdog.choose(pulls, runs, NOW, OWNER, BOT, jobs)]


def test_a_ready_head_without_a_verdict_gets_its_review_started_once():
    """On 2026-11-04 a ready mark started no review, and the pull request waited about four hours."""
    assert choose(pull(7)) == [(7, "dispatch")]
    assert watchdog.choose([pull(7)], [], NOW, OWNER, BOT)[0]["marker"] == f"<!-- review-watchdog:dispatch:{7:040d} -->"
    assert choose(pull(7, comments=(said(7, "dispatch", FRESH),))) == [], "dispatched 10 minutes ago: it may still come"
    assert choose(pull(7, comments=(said(7, "dispatch", OLD, head="f" * 40),))) == [(7, "dispatch")], \
        "a marker of an older head counts for nothing: a new head may get its own"


def test_a_second_miss_gets_one_comment_for_the_owner_and_no_more_dispatches():
    assert choose(pull(7, comments=(said(7, "dispatch"),))) == [(7, "second-miss")]
    assert choose(pull(7, comments=(said(7, "dispatch"), said(7, "second-miss")))) == [], "once per head"
    assert choose(pull(7, comments=(said(7, "dispatch", login=OWNER),))) == [(7, "dispatch")], \
        "only the watchdog's own comments hold its state"
    assert choose(pull(7, comments=(said(7, "dispatch", login=BOT + "[bot]"),))) == [(7, "second-miss")], \
        "REST's spelling of the app's login is the same app"


def test_a_draft_a_head_with_a_verdict_and_a_running_review_are_left_alone():
    assert choose(pull(7, draft=True)) == []
    assert choose(pull(7, verdict=True)) == [], "a review ran or runs: its `start` writes the check"
    assert choose(pull(7), runs=(run(7),)) == [], "a review for it is running"
    assert choose(pull(7), runs=(run(7, status="queued"),)) == [], "or queued"
    assert choose(pull(7), runs=(run(7, status="completed"),)) == [(7, "dispatch")], "a finished run is no review now"
    assert choose(pull(7), runs=(run(None, title="Claude Code Review by hand: PR #7, round 1", event="workflow_dispatch"),)) == [], \
        "a run by hand names its pull request in its title"


def test_a_review_that_names_no_pull_request_holds_every_dispatch():
    """It may be one of these pull requests' reviews, and a second review would count a round."""
    nameless = run(None, title="Claude Code Review", event="workflow_dispatch")
    assert choose(pull(7), runs=(nameless,)) == []
    assert choose(pull(7, comments=(said(7, "dispatch"),)), runs=(nameless,)) == [(7, "second-miss")], \
        "a comment starts nothing: it still goes"


def test_the_wait_counts_from_the_last_ready_mark_or_push():
    assert choose(pull(7, pushed=FRESH)) == [], "pushed 10 minutes ago"
    assert choose(pull(7, ready=("2026-11-05T10:00:00Z", FRESH))) == [], "the last ready mark was 10 minutes ago"
    assert choose(pull(7, ready=("2026-11-05T10:00:00Z",))) == [(7, "dispatch")]


def test_the_owners_approve_on_github_gets_one_note_and_no_dispatch():
    """The owner's answer (2026-11-04): the label owner-approved stays the one signal; the watchdog says so, once
    per head, and never adds it."""
    assert choose(pull(7, reviews=(approve(7),))) == [(7, "approve-note")]
    assert choose(pull(7, reviews=(approve(7),), labels=("escalated",))) == [(7, "approve-note")], "escalated too"
    assert choose(pull(7, reviews=(approve(7),), comments=(said(7, "approve-note"),))) == [], "once per head"
    assert choose(pull(7, reviews=(approve(7),), labels=("owner-approved",))) == [(7, "dispatch")], \
        "the label is there: its own run should have written the check"
    assert choose(pull(7, reviews=(approve(7, login="someone"),))) == [(7, "dispatch")], "only the owner's Approve"
    assert choose(pull(7, reviews=(approve(7, head="f" * 40),))) == [(7, "dispatch")], "an Approve of an older head"


def test_only_the_head_branchs_own_commit_counts():
    """The head's checks come from the head branch's commit; a branch that is gone, or that moved past
    the pull request's head since, waits for the next run."""
    assert choose({**pull(7), "headRef": None}) == []
    moved = pull(7)
    moved["headRef"]["target"]["oid"] = "f" * 40
    assert choose(moved) == []


def test_an_escalated_pull_request_waits_on_the_owner():
    assert choose(pull(7, labels=("escalated",))) == []


def test_each_pull_request_is_chosen_on_its_own():
    assert choose(pull(7), pull(8, verdict=True), pull(9, reviews=(approve(9),))) == [(7, "dispatch"), (9, "approve-note")]


def gates_run(run_id: int, head: str, *, conclusion: str = "failure", attempt: int = 1, ended: str = OLD,
              status: str = "completed", event: str = "pull_request") -> dict:
    return {"id": run_id, "head_sha": head, "event": event, "status": status, "conclusion": conclusion,
            "run_attempt": attempt, "updated_at": ended}


def test_a_report_that_failed_after_the_reviews_verdict_is_rerun_once():
    """On 2026-11-16 a review approved at 03:36, then the job `report` failed in its checkout (attempt
    1), the head kept the check "Under review" in progress, and the pull request waited 4.7 hours.
    Its shape: the reviewer app's APPROVED (and a COMMENTED) review on the head, the check in progress, the run failed."""
    failed = run(7, status="completed", conclusion="failure", run_id=43798756662)
    reviews = (verdict_review(7), {"author": {"login": BOT}, "state": "COMMENTED", "submittedAt": "2026-11-05T11:45:06Z",
                                   "commit": {"oid": f"{7:040d}"}})
    stuck = pull(7, verdict="IN_PROGRESS", reviews=reviews)
    report_failed = {"43798756662": jobs("report")}
    assert choose(stuck, runs=(failed,), jobs=report_failed) == [(7, "rerun-report")]
    assert watchdog.choose([stuck], [failed], NOW, OWNER, BOT, report_failed)[0]["run"] == 43798756662
    assert choose(pull(7, verdict="IN_PROGRESS", reviews=reviews, comments=(said(7, "rerun-report", "2026-11-05T11:55:00Z"),)),
                  runs=(failed,), jobs=report_failed) == [], "once per head: the rerun starts"
    # A rerun whose call failed leaves the run a failed attempt 1 under the marker: the owner is told
    assert choose(pull(7, verdict="IN_PROGRESS", reviews=reviews, comments=(said(7, "rerun-report"),)), runs=(failed,),
                  jobs=report_failed) == [(7, "report-miss")], "the rerun never started"
    assert choose(pull(7, verdict="IN_PROGRESS", reviews=reviews,
                       comments=(said(7, "rerun-report"), said(7, "report-miss"))), runs=(failed,),
                  jobs=report_failed) == [], "told once"
    assert choose(pull(7, verdict="IN_PROGRESS", reviews=(verdict_review(7, "CHANGES_REQUESTED"),)), runs=(failed,),
                  jobs=report_failed) == [(7, "rerun-report")], "a request for changes needs its check too"
    assert choose(pull(7, verdict="IN_PROGRESS", reviews=(verdict_review(7, when="2026-11-05T11:55:00Z"),)),
                  runs=(failed,), jobs=report_failed) == [], "5 minutes after the review: report may still run"
    assert choose(pull(7, verdict="IN_PROGRESS", reviews=(verdict_review(7, head="f" * 40),)), runs=(failed,),
                  jobs=report_failed) == [], "a review of an older head is no verdict of this one"
    assert choose(stuck, runs=(failed,), jobs={}) == [], "the jobs were not read: the next pass decides"


def test_a_head_with_its_check_or_a_running_review_is_left_alone():
    failed = run(7, status="completed", conclusion="failure")
    report_failed = {"1": jobs("report")}
    assert choose(pull(7, verdict="COMPLETED", reviews=(verdict_review(7),)), runs=(failed,), jobs=report_failed) == []
    assert choose(pull(7, verdict="IN_PROGRESS", reviews=(verdict_review(7),)),
                  runs=(failed, run(7, run_id=2)), jobs=report_failed) == [], "a review runs for it: the rerun, maybe"
    assert choose(pull(7, draft=True, verdict="IN_PROGRESS", reviews=(verdict_review(7),)), runs=(failed,),
                  jobs=report_failed) == []


def test_a_report_that_failed_again_or_a_run_not_found_gets_one_comment_for_the_owner():
    reviews = (verdict_review(7),)
    again = run(7, status="completed", conclusion="failure", attempt=2)
    assert choose(pull(7, verdict="IN_PROGRESS", reviews=reviews), runs=(again,), jobs={"1": jobs("report")}) == \
        [(7, "report-miss")], "attempt 2: the rerun failed too"
    assert choose(pull(7, verdict="IN_PROGRESS", reviews=reviews)) == [(7, "report-miss")], "no run of it in the list"
    assert choose(pull(7, verdict="IN_PROGRESS", reviews=reviews, comments=(said(7, "report-miss"),))) == [], \
        "once per head"
    other = run(7, status="completed", conclusion="failure")
    assert choose(pull(7, verdict="IN_PROGRESS", reviews=reviews), runs=(other,), jobs={"1": jobs("carry")}) == \
        [(7, "report-miss")], "another job failed: nothing to rerun"
    # The review gave its verdict, then its job timed out, and report failed too: a rerun of the failed
    # jobs would run the review again, a second review
    assert choose(pull(7, verdict="IN_PROGRESS", reviews=reviews), runs=(other,), jobs={"1": jobs("review", "report")}) \
        == [(7, "report-miss")], "never a second review"


def test_a_review_job_that_failed_with_no_verdict_keeps_todays_path():
    """No review of the reviewer app on the head: the job `review` gave no verdict, and the watchdog does what it did
    before, never a rerun of the review."""
    failed = run(7, status="completed", conclusion="failure")
    review_failed = {"1": jobs("review", "report")}
    assert choose(pull(7, verdict="IN_PROGRESS"), runs=(failed,), jobs=review_failed) == []
    assert choose(pull(7), runs=(failed,), jobs=review_failed) == [(7, "dispatch")]


def test_the_failed_reviews_whose_jobs_are_read_are_each_pull_requests_latest_in_attempt_1():
    def ids(*runs: dict, pulls: tuple[dict, ...] = (pull(7),)) -> list[int]:
        return watchdog.failed_reviews(pulls, runs)
    assert ids(run(7, status="completed", conclusion="failure", run_id=5)) == [5]
    assert ids(run(7, status="completed", conclusion="failure", run_id=5),
               run(7, status="completed", conclusion="success", run_id=6)) == [], "only the latest run"
    assert ids(run(7, status="completed", conclusion="failure", run_id=5, attempt=2)) == []
    assert ids(run(None, title="Claude Code Review: PR #7, round 1", status="completed", conclusion="failure",
                   run_id=5, event="workflow_dispatch")) == [5], "a run by hand names its pull request in its title"
    assert ids(run(8, status="completed", conclusion="failure", run_id=5)) == [], "no open pull request of it"
    assert ids(run(7, status="completed", conclusion="failure"), pulls=(pull(7, draft=True),)) == []


def stalled(pulls: tuple[dict, ...], *runs: dict) -> list[int]:
    return watchdog.stalled_gates(pulls, runs, NOW)


def test_a_current_heads_gates_run_that_failed_in_attempt_1_is_checked_once_gates_retry_had_its_turn():
    """On 2026-11-09 GitHub failed a pull request's gates run at startup (no job), gates-retry failed at startup too,
    and the pull request waited two hours."""
    head = f"{7:040d}"
    pulls = (pull(7),)
    assert stalled(pulls, gates_run(1, head)) == [1]
    assert stalled(pulls, gates_run(1, head, conclusion="startup_failure")) == [1]
    assert stalled(pulls, gates_run(1, head, ended="2026-11-05T11:58:00Z")) == [], "2 minutes ago: gates-retry's turn"
    assert stalled(pulls, gates_run(1, head, attempt=2)) == [], "rerun once: attempt 2 is the owner's"
    assert stalled(pulls, gates_run(1, head, conclusion="success")) == []
    assert stalled(pulls, gates_run(1, head, status="in_progress", conclusion="")) == []
    assert stalled(pulls, gates_run(1, "f" * 40)) == [], "an outdated head: its rerun would cancel the new head's gates"
    assert stalled(pulls, gates_run(1, head, event="push")) == []
    assert stalled(pulls, gates_run(1, head), gates_run(2, head, conclusion="success")) == [], "only the head's latest run"
    assert stalled((pull(7), pull(8)), gates_run(1, head), gates_run(2, f"{8:040d}")) == [1, 2]


def run_step(tmp_path: Path, pulls: list[dict], runs: list[dict] | None = None, fail: str | None = None,
             name: str = STEP, more: list | None = None,
             extra: dict[str, str] | None = None) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """A step's command as GitHub runs it (bash -e, pipefail) in a folder with main's script, gh faked."""
    fake, bin_, work = tmp_path / "fake", tmp_path / "bin", tmp_path / "work"
    for folder in (fake, bin_, work / ".github"):
        folder.mkdir(parents=True)
    (work / ".github" / "review_watchdog.py").write_text((REAL_REPO / ".github" / "review_watchdog.py").read_text())
    rules = [("claude-code-review.yml/runs", json.dumps({"workflow_runs": runs or []}), 0),
             ("api graphql", json.dumps({"data": {"repository": {"pullRequests": {"nodes": pulls}}}}), 0)]
    if fail:
        rules.insert(0, (fail, "", 1))
    rules[:0] = more or []
    (fake / "rules.json").write_text(json.dumps(rules))
    if name != STEP:  # the review step wrote the pull requests
        (work / "pulls.json").write_text(rules[-1][1])
    (fake / "log").write_text("")
    (bin_ / "gh").write_text(LOGGING_GH)
    (bin_ / "gh").chmod(0o755)
    (bin_ / "python3").symlink_to(sys.executable)
    env = {"PATH": f"{bin_}:/usr/bin:/bin", "FAKE": str(fake), "GITHUB_REPOSITORY": "owner/repo", "GH_TOKEN": "token",
           "OWNER": OWNER, "MAIN": "main", "REVIEWER": "maurolopes-reviewer[bot]", "DISPATCH": "dispatch-token", **(extra or {})}
    done = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", COMMANDS[name]],
                          cwd=work, env=env, capture_output=True, text=True, check=False)
    return done, (fake / "log").read_text().splitlines()


def test_the_step_comments_with_the_marker_then_starts_the_review(tmp_path: Path):
    # the step reads the real clock (`date`): times well in the past
    past = {"pushed": "2026-01-01T00:00:00Z", "opened": "2026-01-01T00:00:00Z"}
    done, calls = run_step(tmp_path / "one", [pull(7, **past), pull(8, verdict=True, **past),
                                              pull(9, reviews=(approve(9),), **past)])
    assert done.returncode == 0, done.stderr
    comments = [c for c in calls if c.startswith("pr comment")]
    assert len(comments) == 2 and comments[0].startswith("pr comment 7 ") and comments[1].startswith("pr comment 9 ")
    assert f"<!-- review-watchdog:dispatch:{7:040d} -->" in comments[0] and "started one" in comments[0]
    assert "add the label owner-approved" in comments[1] and f"approve-note:{9:040d}" in comments[1]
    starts = [c for c in calls if c.startswith("workflow run")]
    assert starts == ["workflow run claude-code-review.yml -R owner/repo --ref main -f pr=7"], "a dispatch for 7 only"
    assert calls.index(comments[0]) < calls.index(starts[0]), "the comment first: it is the head's state"
    tokens = (tmp_path / "one" / "fake" / "tokens").read_text().splitlines()
    assert all(t.startswith("dispatch-token ") for t in tokens if " workflow run " in f" {t} "), "GitHub Actions' token starts it"
    assert all(t.startswith("token ") for t in tokens if " pr comment " in f" {t} "), "the reviewer app's token comments"
    # Listing the runs needs Actions read, which only GitHub Actions' token has here
    assert any(t.startswith("dispatch-token ") and "claude-code-review.yml/runs" in t for t in tokens)
    assert any(t.startswith("token ") and t.split()[1:3] == ["api", "graphql"] for t in tokens), "the app reads the pulls"


def test_the_step_reads_the_failed_runs_jobs_then_comments_and_reruns_the_failed_report(tmp_path: Path):
    """GitHub Actions' token reads the jobs and reruns the failed ones of the run; the reviewer app comments."""
    past = {"pushed": "2026-01-01T00:00:00Z", "opened": "2026-01-01T00:00:00Z"}
    stuck = pull(7, verdict="IN_PROGRESS", reviews=(verdict_review(7, when="2026-01-01T01:00:00Z"),), **past)
    failed = run(7, status="completed", conclusion="failure", run_id=99)
    done, calls = run_step(tmp_path / "one", [stuck], runs=[failed], more=[("runs/99/jobs", json.dumps(jobs("report")), 0)])
    assert done.returncode == 0, done.stderr
    comments = [c for c in calls if c.startswith("pr comment")]
    assert len(comments) == 1 and "runs the failed jobs of run 99 again" in comments[0] and \
        f"<!-- review-watchdog:rerun-report:{7:040d} -->" in comments[0]
    reruns = [c for c in calls if c.startswith("run rerun")]
    assert reruns == ["run rerun 99 --failed -R owner/repo"]
    assert calls.index(comments[0]) < calls.index(reruns[0]), "the comment first: it is the head's state"
    assert not any(c.startswith("workflow run") for c in calls), "no second review"
    tokens = (tmp_path / "one" / "fake" / "tokens").read_text().splitlines()
    assert any(t.startswith("dispatch-token ") and "runs/99/jobs" in t for t in tokens), "Actions read: GitHub's token"
    assert all(t.startswith("dispatch-token ") for t in tokens if " run rerun " in f" {t} ")
    done, calls = run_step(tmp_path / "unread", [stuck], runs=[failed], more=[("runs/99/jobs", "", 1)])
    assert done.returncode == 0, done.stderr
    assert not any(c.startswith(("pr comment", "run rerun")) for c in calls), "jobs unread: the next pass decides"


def test_a_failed_comment_starts_no_review(tmp_path: Path):
    """Without the comment the next run would not know of the dispatch, and would start the review again."""
    done, calls = run_step(tmp_path / "fail", [pull(7, pushed="2026-01-01T00:00:00Z", opened="2026-01-01T00:00:00Z")],
                           fail="pr comment")
    assert done.returncode == 0, done.stderr
    assert not any(c.startswith("workflow run") for c in calls) and "comment failed" in done.stdout


def test_a_failed_dispatch_a_failed_report_rerun_and_a_reply_that_is_no_json_only_warn(tmp_path: Path):
    """The shell's warnings stay: the step goes on, exits 0, and says what to do by hand (the same-warnings rule)."""
    past = {"pushed": "2026-01-01T00:00:00Z", "opened": "2026-01-01T00:00:00Z"}
    done, calls = run_step(tmp_path / "dispatch", [pull(7, **past)], fail="workflow run")
    assert done.returncode == 0 and "the review did not start: start it by hand" in done.stdout
    assert any(c.startswith("pr comment 7 ") for c in calls), "the comment came first and stays"
    stuck = pull(7, verdict="IN_PROGRESS", reviews=(verdict_review(7, when="2026-01-01T01:00:00Z"),), **past)
    failed = run(7, status="completed", conclusion="failure", run_id=99)
    done, calls = run_step(tmp_path / "rerun", [stuck], runs=[failed], fail="run rerun",
                           more=[("runs/99/jobs", json.dumps(jobs("report")), 0)])
    assert done.returncode == 0 and "the rerun of review run 99 failed: rerun its failed jobs by hand" in done.stdout
    done, calls = run_step(tmp_path / "json", [stuck], runs=[failed], more=[("runs/99/jobs", "not json", 0)])
    assert done.returncode == 0 and "review run 99: its jobs were not read" in done.stdout
    assert not any(c.startswith(("pr comment", "run rerun")) for c in calls)


def test_the_error_text_of_a_failed_gh_call_reaches_the_steps_log_next_to_the_warning(monkeypatch, capsys):
    """The stderr of gh went to the step's log when the shell ran gh. A dispatch that GitHub refuses (HTTP 403)
    keeps its reason there, and a read that fails prints it once."""
    def refuse(argv, **kwargs):
        ok = argv[1:3] == ["pr", "comment"]  # the comment works, the dispatch and the reads do not
        return subprocess.CompletedProcess(argv, 0 if ok else 1, "", "" if ok else "HTTP 403: Resource not accessible\n")
    monkeypatch.setattr(watchdog.subprocess, "run", refuse)
    action = {"pr": 7, "head": "a" * 40, "action": "dispatch", "marker": "m"}
    watchdog.act({"GITHUB_REPOSITORY": "owner/repo", "MAIN": "main", "DISPATCH": "t"}, action)
    seen = capsys.readouterr()
    assert "HTTP 403: Resource not accessible" in seen.err and "the review did not start" in seen.out
    assert watchdog.watch({"GITHUB_REPOSITORY": "owner/repo"}, NOW) == 1
    assert capsys.readouterr().err.count("HTTP 403") == 1, "once, not twice"


def test_an_unknown_action_warns_and_calls_nothing(capsys):
    watchdog.act({"GITHUB_REPOSITORY": "owner/repo", "MAIN": "main"}, {"pr": 7, "head": "a" * 40, "action": "bogus", "marker": "m"})
    assert "::warning::unknown action bogus" in capsys.readouterr().out


def test_the_step_reruns_only_a_stalled_gates_run_that_has_no_job(tmp_path: Path):
    """The rule picks the runs; the step reruns the whole run only when it has no job, so a red gate stays red."""
    past = {"pushed": "2026-01-01T00:00:00Z", "opened": "2026-01-01T00:00:00Z"}
    gates = {"workflow_runs": [gates_run(11, f"{7:040d}", ended="2026-01-01T01:00:00Z"),
                               gates_run(12, f"{8:040d}", ended="2026-01-01T01:00:00Z")]}
    more = [("gates.yml/runs", json.dumps(gates), 0), ("runs/11/jobs", "0", 0), ("runs/12/jobs", "2", 0)]
    done, calls = run_step(tmp_path / "gates", [pull(7, **past), pull(8, **past)], name=GATES_STEP, more=more)
    assert done.returncode == 0, done.stderr
    assert [c for c in calls if c.startswith("run rerun")] == ["run rerun 11 -R owner/repo"], "run 12 ran its jobs"
    assert "gates run 11 had no job" in done.stdout
    tokens = (tmp_path / "gates" / "fake" / "tokens").read_text().splitlines()
    assert all(t.startswith("token ") for t in tokens), "GitHub Actions' token (the step's GH_TOKEN), never the app's"
    done, calls = run_step(tmp_path / "fail", [pull(7, **past)], name=GATES_STEP, fail="run rerun", more=more)
    assert done.returncode == 0 and "rerun it by hand" in done.stdout, "a failed rerun warns, and the step goes on"


def test_the_gates_step_reads_the_runs_of_gates_workflow(tmp_path: Path):
    """Another product names its own gates workflow (GATES_WORKFLOW); the default is this product's gates.yml."""
    past = {"pushed": "2026-01-01T00:00:00Z", "opened": "2026-01-01T00:00:00Z"}
    gates = {"workflow_runs": [gates_run(11, f"{7:040d}", ended="2026-01-01T01:00:00Z")]}
    more = [("workflows/checks.yml/runs", json.dumps(gates), 0), ("runs/11/jobs", "0", 0)]
    done, calls = run_step(tmp_path / "other", [pull(7, **past)], name=GATES_STEP, more=more,
                           extra={"GATES_WORKFLOW": "checks.yml"})
    assert done.returncode == 0, done.stderr
    assert [c for c in calls if c.startswith("run rerun")] == ["run rerun 11 -R owner/repo"]
    assert not any("gates.yml" in c for c in calls)


def test_the_workflow_runs_mains_code_every_15_minutes_and_writes_no_check():
    flow = yaml.safe_load(WATCHDOG.read_text())
    triggers = flow[True]  # YAML 1.1 reads the key `on` as True
    assert set(triggers) == {"schedule", "workflow_dispatch"}, "never a pull request's code"
    minutes = triggers["schedule"][0]["cron"].split()[0].split(",")
    assert len(minutes) == 4 and all(int(b) - int(a) == 15 for a, b in pairwise(minutes))
    job = flow["jobs"]["watch"]
    assert job["permissions"] == {"contents": "read", "actions": "write"}, "no checks or pull requests write"
    token = next(s for s in job["steps"] if s.get("id") == "token")["with"]
    assert {k: v for k, v in token.items() if k.startswith("permission-")} == \
        {"permission-pull-requests": "write", "permission-contents": "read", "permission-checks": "read"}, \
        "the app's token: comments, and reading the head commit"
    for step in job["steps"]:
        assert "--add-label" not in step.get("run", "") and "labels" not in step.get("run", ""), "never a label"
    gates = next(s for s in job["steps"] if s.get("name") == GATES_STEP)
    assert gates["env"] == {"GH_TOKEN": "${{ github.token }}"} and gates["if"] == "${{ !cancelled() }}", \
        "GitHub Actions' token reruns; a failed review step does not stop the gates' rerun"


def test_each_step_only_calls_mains_script_which_needs_no_other_file():
    """The logic of both steps is in review_watchdog.py, so a change of the rules needs no workflow patch. The
    checkout takes that one file, and the script reads nothing else of the repository (standard library only)."""
    job = yaml.safe_load(WATCHDOG.read_text())["jobs"]["watch"]
    login = 'echo "REVIEWER=${SLUG}[bot]" >> "$GITHUB_ENV"'  # the reviewer app's login from its token: test_ci.py
    assert {s["name"]: s["run"] for s in job["steps"] if "run" in s and s["run"].strip() != login} == COMMANDS
    checkout = next(s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout@"))
    assert checkout["with"]["sparse-checkout"] == ".github/review_watchdog.py"


def test_a_failed_read_ends_the_watch_with_gh_s_code_and_no_comment(tmp_path: Path):
    """The shell's `set -e` ended the step at a failed read: the pull requests, or the review workflow's runs."""
    past = {"pushed": "2026-01-01T00:00:00Z", "opened": "2026-01-01T00:00:00Z"}
    for part in ("api graphql", "claude-code-review.yml/runs"):
        done, calls = run_step(tmp_path / part[:6], [pull(7, **past)], fail=part)
        assert done.returncode == 1, (part, done.stdout)
        assert not any(c.startswith(("pr comment", "workflow run")) for c in calls), part


def test_the_gates_step_reads_nothing_without_pulls_and_fails_on_a_failed_read(tmp_path: Path):
    """The step runs after a failed review step (`!cancelled()`), when pulls.json may be missing or empty."""
    work = tmp_path / "work"
    work.mkdir()
    done = subprocess.run([sys.executable, "-I", str(REAL_REPO / ".github" / "review_watchdog.py"), "rerun-gates"], cwd=work,
                          env={"GITHUB_REPOSITORY": "owner/repo", "PATH": "/usr/bin:/bin"}, capture_output=True, text=True, check=False)
    assert done.returncode == 0 and "no pull requests read" in done.stdout
    (work / "pulls.json").write_text("")
    done = subprocess.run([sys.executable, "-I", str(REAL_REPO / ".github" / "review_watchdog.py"), "rerun-gates"], cwd=work,
                          env={"GITHUB_REPOSITORY": "owner/repo", "PATH": "/usr/bin:/bin"}, capture_output=True, text=True, check=False)
    assert done.returncode == 0 and "no pull requests read" in done.stdout
    past = {"pushed": "2026-01-01T00:00:00Z", "opened": "2026-01-01T00:00:00Z"}
    done, calls = run_step(tmp_path / "read", [pull(7, **past)], name=GATES_STEP, fail="gates.yml/runs")
    assert done.returncode == 1 and not any(c.startswith("run rerun") for c in calls)
    done, calls = run_step(tmp_path / "jobs", [pull(7, **past)], name=GATES_STEP, fail="runs/11/jobs",
                           more=[("gates.yml/runs", json.dumps({"workflow_runs": [gates_run(11, f"{7:040d}", ended="2026-01-01T01:00:00Z")]}), 0)])
    assert done.returncode == 1 and not any(c.startswith("run rerun") for c in calls), "jobs unread: no rerun"
