"""The branch update (.github/branch_update.py and .github/workflows/branch-update.yml): after
each merge, the oldest approved pull request that conflicts with main is asked for GitHub's merge of main, one at a time,
and GitHub's refusal sends it back to an implementer. A pull request that is only behind main is left alone: the ruleset
does not need it up to date. The step runs with gh faked, never the network."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import yaml

from tests.conftest import REAL_REPO
from tests.test_ci import FAKE_LINEAR, LOGGING_GH
from tests.test_verdict import verdict

UPDATE = REAL_REPO / ".github" / "workflows" / "branch-update.yml"
STEP = "Find the next pull request that conflicts with main"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("github_branch_update", REAL_REPO / ".github" / "branch_update.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


update = load()


def check_run(name: str, conclusion: str | None, ident: int, status: str = "COMPLETED", app: str | None = "github-actions") -> dict:
    """One check run as the query reads it; a higher id is a newer run."""
    return {"databaseId": ident, "name": name, "status": status, "conclusion": conclusion,
            "checkSuite": {"app": {"slug": app} if app else None}}


# the head's checks as each state the tests name: check runs, and a commit status for ERROR and EXPECTED
CHECKS = {"SUCCESS": [check_run("python", "SUCCESS", 10)], "FAILURE": [check_run("python", "FAILURE", 10)],
          "PENDING": [check_run("python", None, 10, "IN_PROGRESS")],
          "ERROR": [{"context": "ci/legacy", "state": "ERROR"}], "EXPECTED": [{"context": "ci/legacy", "state": "EXPECTED"}]}


def pull(number: int, behind: int = 1, auto: str | None = "2026-10-28T10:00:00Z", checks: str | None = "SUCCESS",
         draft: bool = False, verdict: str | None = "SUCCESS", app: str | None = None, mergeable: str | None = None,
         labels: tuple[str, ...] = (), runs: list[dict] | None = None) -> dict:
    """One pull request as the query reads it. GraphQL gives the reviewer app's check `app: null` (measured 2026-10-28).
    `runs` replaces the head's checks that `checks` names, and the verdict's when it holds one. `behind` names the pull
    requests that main does not merge into cleanly: 1 and more, GitHub's CONFLICTING; 0, MERGEABLE (`mergeable` says other)."""
    contexts = list(runs if runs is not None else CHECKS[checks] if checks else [])
    if verdict and checks and not any(c.get("name") == "verdict" for c in contexts):
        contexts.append(check_run("verdict", verdict, 20, app=app))
    rollup = {"contexts": {"nodes": contexts}} if contexts else None
    return {"number": number, "isDraft": draft, "headRefOid": f"{number:040d}", "mergeable": mergeable or ("CONFLICTING" if behind else "MERGEABLE"),
            "autoMergeRequest": {"enabledAt": auto} if auto else None,
            "headRef": {"name": f"branch-{number}"}, "labels": {"nodes": [{"name": n} for n in labels]},
            "commits": {"nodes": [{"commit": {"statusCheckRollup": rollup}}]}}


def reply(*pulls: dict) -> dict:
    return {"data": {"repository": {"pullRequests": {"nodes": list(pulls)}}}}


def test_the_oldest_auto_merge_that_conflicts_with_main_goes_first():
    first = pull(9, auto="2026-10-28T09:00:00Z")
    later = pull(3, auto="2026-10-28T11:00:00Z")
    assert update.choose(reply(later, first)) == f"update 9 {9:040d}", "by the time auto-merge went on, not the number"
    assert update.choose(reply(later, first), {9}) == f"update 3 {3:040d}", "a pull request this run tried is skipped"
    assert update.choose(reply(later, first), {9, 3}) == ""


def test_a_pull_request_that_is_only_behind_main_is_left_alone():
    """No update of a clean pull request (it would run the gates again and make the merges serial)."""
    assert update.choose(reply()) == ""
    assert update.choose(reply(pull(4, behind=0), pull(5, behind=0, checks=None))) == ""


def test_a_queue_pull_request_that_github_has_not_computed_is_pending():
    """GitHub computes `mergeable` lazily after main moves, and a run 4 s after a merge read UNKNOWN as no conflict.
    With nothing to update, a queue pull request that reads UNKNOWN makes the choice `pending`, so the workflow reads again.
    A conflict goes first; a pull request outside the queue (a draft, no approve, failed gates, tried already) never waits."""
    assert update.choose(reply(pull(6, mergeable="UNKNOWN"), owners(7, mergeable="UNKNOWN"))) == "pending 6 7"
    assert update.choose(reply(pull(6, mergeable="UNKNOWN"), pull(4))) == f"update 4 {4:040d}", "a conflict first"
    assert update.choose(reply(pull(6, mergeable="UNKNOWN"), owners(3))) == f"update 3 {3:040d} escalated"
    assert update.choose(reply(pull(6, mergeable="UNKNOWN"), pull(4)), {4}) == "pending 6"
    assert update.choose(reply(pull(6, mergeable="UNKNOWN")), {6}) == "", "tried already"
    for left in (pull(1, mergeable="UNKNOWN", draft=True), pull(2, mergeable="UNKNOWN", auto=None),
                 pull(3, mergeable="UNKNOWN", checks="FAILURE"), pull(5, mergeable="UNKNOWN", verdict=None),
                 owners(8, mergeable="UNKNOWN", verdict="FAILURE")):
        assert update.choose(reply(left)) == "", left["number"]


def owners(number: int, **more) -> dict:
    """An approved pull request that waits for the owner's merge: the label `escalated` and no auto-merge."""
    return pull(number, auto=None, labels=("workflow", "escalated"), **more)


def test_a_pull_request_that_waits_for_the_owners_merge_is_updated_after_the_auto_merges():
    """The owner finds an escalated pull request up to date with main. The auto-merge ones go first, since they
    merge by themselves; then the oldest escalated one."""
    auto = pull(9)
    assert update.choose(reply(owners(3), auto)) == f"update 9 {9:040d}", "an auto-merge first, though younger"
    assert update.choose(reply(owners(3), auto), {9}) == f"update 3 {3:040d} escalated"
    assert update.choose(reply(owners(7), owners(3))) == f"update 3 {3:040d} escalated", "the oldest first"
    assert update.choose(reply(owners(7), owners(3)), {3}) == f"update 7 {7:040d} escalated"
    assert update.choose(reply(owners(3, behind=0))) == "", "up to date"
    assert update.choose(reply(pull(3, auto=None))) == "", "no label: not approved yet, or the owner's answer in work"


def test_a_needs_decision_and_an_unfinished_escalated_pull_request_are_left_out():
    """A needs-decision or a third round carries `escalated` too, with a failed `verdict` or none; a draft or failed gates
    are an implementer's round first."""
    for left in (owners(3, verdict="FAILURE"), owners(3, verdict=None), owners(3, draft=True), owners(3, checks="FAILURE")):
        assert update.choose(reply(left)) == ""


def test_a_draft_no_auto_merge_and_failed_checks_are_left_out():
    """A draft is in its fix round; no auto-merge is no approve yet, or a code owner's file the owner merges; a failed check
    is the implementer's fix round first."""
    assert update.choose(reply(pull(1, draft=True), pull(2, auto=None), pull(3, checks="FAILURE"), pull(4, checks="ERROR"))) == ""
    assert update.choose(reply(pull(1, draft=True), pull(5, checks="PENDING"))) == f"update 5 {5:040d}", "a running check is no failure"
    gone = pull(6)
    gone["headRef"] = None
    assert update.choose(reply(gone)) == "", "a deleted branch"


def test_a_head_without_a_successful_verdict_never_takes_the_updates():
    """A head the approve does not cover (a push after it, or an update `carry` refused)
    can never merge; as the oldest it would take every update, and the younger pull requests would wait for ever."""
    stuck = pull(1, auto="2026-10-28T08:00:00Z", verdict=None)
    assert update.choose(reply(stuck, pull(2))) == f"update 2 {2:040d}"
    assert update.choose(reply(pull(1, auto="2026-10-28T08:00:00Z", verdict="FAILURE"), pull(2))) == f"update 2 {2:040d}"
    assert update.choose(reply(pull(1, checks=None))) == "", "no check at all: no verdict"
    assert update.choose(reply(pull(1, app="github-actions"))) == "", "a workflow job named verdict is not the reviewer's check"
    assert update.choose(reply(pull(1, app="maurolopes-reviewer"))) == f"update 1 {1:040d}", "a name GraphQL shows"


def test_a_cancelled_run_that_a_newer_run_replaced_does_not_count():
    """A workflow whose concurrency group cancels the older run leaves that run on the head. GitHub's
    rollup then says FAILURE, and the approved pull request waited for a person. Per check, only the newest run counts."""
    replaced = [check_run("attribution", "CANCELLED", 1), check_run("attribution", "SUCCESS", 2), check_run("python", "SUCCESS", 3)]
    assert update.checks(pull(3, runs=replaced)) == "SUCCESS"
    assert update.choose(reply(pull(3, runs=replaced))) == f"update 3 {3:040d}"
    assert update.choose(reply(pull(3, runs=list(reversed(replaced))))) == f"update 3 {3:040d}", "by the id, not the order"
    rerun = [check_run("attribution", "SUCCESS", 1), check_run("attribution", "CANCELLED", 2)]
    assert update.choose(reply(pull(3, runs=rerun))) == "", "a cancelled run with no newer one is still a failure"


def test_a_skipped_run_replaces_a_cancelled_run_but_never_a_failed_one():
    """On a docs-only pull request a reopen cancels the gates' first run in their concurrency group, and the newer
    run skips `python` and `web`. The cancelled run gave no result, so the skipped one counts; a failure still counts."""
    docs_only = [check_run("python", "CANCELLED", 1), check_run("python", "SKIPPED", 2), check_run("verdict", "SUCCESS", 3, app=None)]
    assert update.checks(pull(3, runs=docs_only)) == "SUCCESS"
    assert update.choose(reply(pull(3, runs=docs_only))) == f"update 3 {3:040d}"
    assert update.choose(reply(pull(3, runs=list(reversed(docs_only))))) == f"update 3 {3:040d}", "by the id, not the order"
    for conclusion in sorted(update.FAILED_RUN - {"CANCELLED"}):
        failed = [check_run("python", conclusion, 1), check_run("python", "SKIPPED", 2), check_run("verdict", "SUCCESS", 3, app=None)]
        assert update.checks(pull(3, runs=failed)) == "FAILURE", conclusion
        assert update.choose(reply(pull(3, runs=failed))) == "", conclusion
    later = [check_run("python", "CANCELLED", 1), check_run("python", "SKIPPED", 2), check_run("python", "FAILURE", 3)]
    assert update.checks(pull(3, runs=later)) == "FAILURE", "a newer run that failed replaces the skipped one"


def test_a_head_whose_newest_run_of_a_check_failed_is_left_out():
    """An older success does not hide a newer failure; the same name from another app is another check."""
    for conclusion in ("FAILURE", "TIMED_OUT", "STARTUP_FAILURE", "ACTION_REQUIRED"):
        failed = [check_run("python", "SUCCESS", 1), check_run("python", conclusion, 2)]
        assert update.checks(pull(3, runs=failed)) == "FAILURE", conclusion
        assert update.choose(reply(pull(3, runs=failed))) == "", conclusion
    skipped = [check_run("review", "FAILURE", 1), check_run("review", "SKIPPED", 2)]
    assert update.checks(pull(3, runs=skipped)) == "FAILURE", "a job another event skips does not replace the run that failed"
    assert update.checks(pull(3, runs=[check_run("review", "SKIPPED", 1), check_run("review", "SUCCESS", 2)])) == "SUCCESS"
    other = [check_run("python", "FAILURE", 1, app="another-app"), check_run("python", "SUCCESS", 2)]
    assert update.checks(pull(3, runs=other)) == "FAILURE"
    assert update.checks(pull(3, runs=[{"context": "ci/legacy", "state": "FAILURE"}, check_run("python", "SUCCESS", 2)])) == "FAILURE"
    assert update.checks(pull(3, runs=[check_run("python", "SKIPPED", 1), check_run("web", "NEUTRAL", 2)])) == "SUCCESS"


def test_a_head_with_a_check_still_running_is_pending():
    """A pull request whose newest run of a check still runs is pending, also when an older run of that check is done; a
    conflicting one is not a failure and gets its update."""
    for status in ("QUEUED", "IN_PROGRESS", "WAITING", "PENDING", "REQUESTED"):
        running = [check_run("python", "SUCCESS", 1), check_run("python", None, 2, status)]
        assert update.checks(pull(4, runs=running)) == "PENDING", status
    running = [check_run("python", None, 1, "IN_PROGRESS"), check_run("verdict", "SUCCESS", 2, app=None)]
    assert update.choose(reply(pull(4, runs=running))) == f"update 4 {4:040d}"
    assert update.checks(pull(4, runs=[check_run("python", None, 1, "IN_PROGRESS"), check_run("web", "FAILURE", 2)])) == "FAILURE", \
        "a failure counts before a running check"


def test_only_the_newest_verdict_counts():
    """A newer verdict on the same head replaces the older one, as the ruleset reads a required check."""
    stale = [check_run("verdict", "SUCCESS", 1, app=None), check_run("verdict", "FAILURE", 2, app=None)]
    assert update.choose(reply(pull(3, runs=stale))) == ""
    given = [check_run("verdict", "FAILURE", 1, app=None), check_run("verdict", "SUCCESS", 2, app=None)]
    assert update.choose(reply(pull(3, runs=given))) == f"update 3 {3:040d}"


def test_a_refusal_is_read_from_githubs_words():
    """The texts measured on 2026-10-28 with the implementer app's token."""
    conflict = '{"message":"merge conflict between base and head","status":"422"}gh: merge conflict between base and head (HTTP 422)'
    moved = '{"message":"expected head sha didn’t match current head ref.","status":"422"}gh: expected head sha didn’t match current head ref. (HTTP 422)'
    assert update.refusal(conflict) == "conflict" and update.refusal(moved) == "moved"
    assert update.refusal("gh: Bad credentials (HTTP 401)") == "other"


def test_the_command_line(tmp_path: Path):
    path = tmp_path / "pulls.json"
    path.write_text(json.dumps(reply(pull(9), pull(3))))
    script = str(REAL_REPO / ".github" / "branch_update.py")

    def run(*args: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, "-I", script, *args], input=stdin, capture_output=True, text=True, check=False)

    assert run("choose", str(path)).stdout.strip() == f"update 3 {3:040d}"
    assert run("choose", str(path), "--skip", "3").stdout.strip() == f"update 9 {9:040d}"
    assert run("choose", str(path), "--skip").returncode == 2
    assert run("refusal", stdin="merge conflict between base and head").stdout.strip() == "conflict"
    query = run("query").stdout
    assert "mergeable" in query and "checkSuite { app { slug } }" in query
    assert "mergeStateStatus" not in query and "aheadBy" not in query and "labels(first: 20) { nodes { name } }" in query
    assert "databaseId name status conclusion" in query and "... on StatusContext { context state }" in query


def run_update(tmp_path: Path, rules: list[tuple[str, str, int]], pulls: dict, name: str = STEP,
               more: dict[str, str] | None = None, job: str = "update",
               files: dict[str, str] | None = None) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """A step of the workflow, run as GitHub runs it (bash -e, pipefail) in a folder with main's scripts, gh faked."""
    step = next(s for s in yaml.safe_load(UPDATE.read_text())["jobs"][job]["steps"] if s.get("name") == name)
    fake, bin_, work = tmp_path / "fake", tmp_path / "bin", tmp_path / "work"
    for folder in (fake, bin_, work / ".github"):
        folder.mkdir(parents=True)
    for script in ("branch_update.py", "linear.py"):
        (work / ".github" / script).write_text((REAL_REPO / ".github" / script).read_text())
    for path, text in (files or {}).items():
        (work / path).write_text(text)
    rules = [*rules, ("api graphql", json.dumps(pulls), 0)]
    (fake / "rules.json").write_text(json.dumps(rules))
    (fake / "log").write_text("")
    (bin_ / "gh").write_text(LOGGING_GH)
    (bin_ / "gh").chmod(0o755)
    (bin_ / "python3").symlink_to(sys.executable)
    env = {"PATH": f"{bin_}:/usr/bin:/bin", "FAKE": str(fake), "GITHUB_REPOSITORY": "owner/repo", "GH_TOKEN": "token",
           "LINEAR_API_KEY": "", **(more or {})}
    run = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
                         cwd=work, env=env, capture_output=True, text=True, check=False)
    return run, (fake / "log").read_text().splitlines()


def updates(calls: list[str]) -> list[str]:
    return [c.split()[3] for c in calls if "update-branch" in c]


def test_the_step_updates_one_pull_request(tmp_path: Path):
    run, calls = run_update(tmp_path / "one", [], reply(pull(9), pull(3, auto="2026-10-28T08:00:00Z")))
    assert run.returncode == 0 and updates(calls) == ["repos/owner/repo/pulls/3/update-branch"]
    assert f"api -X PUT repos/owner/repo/pulls/3/update-branch -f expected_head_sha={3:040d}" in calls
    assert "graphql -F owner=owner -F name=repo" in calls[0]
    run, calls = run_update(tmp_path / "behind", [], reply(pull(9, behind=0), pull(4, behind=0, checks="PENDING")))
    assert run.returncode == 0 and updates(calls) == [] and "no pull request conflicts with main" in run.stdout
    run, calls = run_update(tmp_path / "none", [], reply())
    assert run.returncode == 0 and updates(calls) == [] and not any(c.startswith("pr ") for c in calls), "only the read"


def test_a_conflict_goes_back_to_an_implementer_and_the_next_one_is_updated(tmp_path: Path):
    """One comment, auto-merge off (the next approve turns it on again), the issue to In Progress; then the next one."""
    conflict = ("pulls/3/update-branch", '{"message":"merge conflict between base and head","status":"422"}', 1)
    run, calls = run_update(tmp_path, [conflict], reply(pull(9), pull(3, auto="2026-10-28T08:00:00Z")))
    assert run.returncode == 0, run.stdout + run.stderr
    assert updates(calls) == ["repos/owner/repo/pulls/3/update-branch", "repos/owner/repo/pulls/9/update-branch"]
    comments = [c for c in calls if c.startswith("pr comment 3")]
    assert len(comments) == 1 and "pr merge 3 -R owner/repo --disable-auto" in calls
    nine = next(i for i, c in enumerate(calls) if "pulls/9/update-branch" in c)
    assert calls.index(comments[0]) < calls.index("pr merge 3 -R owner/repo --disable-auto") < nine
    # the review counts its rounds again from this comment: its first words are the ones verdict.py reads
    body = comments[0].split("--body ", 1)[1]
    assert body.startswith(verdict.CONFLICT), body


def test_a_conflict_on_a_pull_request_that_waits_for_the_owners_merge_goes_back_to_its_session(tmp_path: Path):
    """(the owner's call, 2026-11-23, which reverses an earlier conflict rule): such a conflict takes the path of any
    other approved pull request, since the orchestrator now wakes the session that opened it. The comment's first words are
    verdict.py's CONFLICT, so the review counts its rounds again as round 1; the label `escalated` goes off, since the
    orchestrator reads it as a wait on the owner; the issue goes to In Progress. It has no auto-merge to turn off. One
    comment per head, matched on its own words: the older comment of such a pull request names the head too, and a pull
    request that carries it still gets the words above. A failed comment changes nothing else, so the next run writes it;
    a failed label removal leaves the pull request in the queue. Then the next pull request."""
    conflict = ("pulls/3/update-branch", '{"message":"merge conflict between base and head","status":"422"}', 1)
    view = [("pr view 3 -R owner/repo --json headRefName", "abc-1239-x\n", 0), ("pr view 3 -R owner/repo --json title", "A title\n", 0)]
    pulls = reply(owners(3), owners(9))
    files = {".github/linear.py": FAKE_LINEAR}  # Linear is never reached: the key is a fake one
    run, calls = run_update(tmp_path / "first", [conflict, *view, ("issues/3/comments", "An older comment\n", 0)], pulls,
                            more={"LINEAR_API_KEY": "key"}, files=files)
    assert run.returncode == 0, run.stdout + run.stderr
    assert updates(calls) == ["repos/owner/repo/pulls/3/update-branch", "repos/owner/repo/pulls/9/update-branch"]
    comments = [c for c in calls if c.startswith("pr comment 3")]
    assert len(comments) == 1 and f"{3:040d}" in comments[0], "one comment per head"
    body = comments[0].split("--body ", 1)[1]
    assert body.startswith(verdict.CONFLICT), body
    assert "round 1" in body and "Auto-merge" not in body, "no auto-merge to turn off"
    assert "api -X DELETE repos/owner/repo/issues/3/labels/escalated" in calls, "the label goes off"
    assert not any(c.startswith("pr merge 3") for c in calls), "there is no auto-merge to turn off"
    assert [c for c in calls if c.startswith("linear ")] == ["linear key-from abc-1239-x A title", "linear state ABC-9 In Progress"]
    # the same head again (a write failed, so the pull request stayed in the queue): said already, and no second move of the
    # same words
    said = ("issues/3/comments", f"An older comment\n{body}\n", 0)
    run, calls = run_update(tmp_path / "again", [conflict, *view, said], pulls, more={"LINEAR_API_KEY": "key"}, files=files)
    assert run.returncode == 0 and not any(c.startswith("pr comment") for c in calls), "the same head: said already"
    assert updates(calls)[-1] == "repos/owner/repo/pulls/9/update-branch"
    # the words that the workflow wrote earlier name the head too, and they are not the words that the orchestrator
    # reads: a pull request that carries them still gets the comment
    older = ("issues/3/comments", "This pull request waits for the owner's merge, and the branch update found a merge conflict "
                                 f"between main and its head {3:040d}.\n", 0)
    run, calls = run_update(tmp_path / "older", [conflict, *view, older], pulls, more={"LINEAR_API_KEY": "key"}, files=files)
    assert run.returncode == 0 and len([c for c in calls if c.startswith("pr comment 3")]) == 1, "the older words hold nothing"
    # a comment that fails changes nothing else: the label stays, the issue stays in Escalated,
    # and the next run meets the same conflict and writes the comment
    run, calls = run_update(tmp_path / "nocomment", [conflict, ("pr comment", "", 1)], pulls,
                            more={"LINEAR_API_KEY": "key"}, files=files)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "::warning::PR #3: the comment on the conflict failed" in run.stdout
    assert not any(c.startswith(("api -X DELETE", "linear ", "pr merge")) for c in calls), "nothing else changed"
    assert updates(calls) == ["repos/owner/repo/pulls/3/update-branch", "repos/owner/repo/pulls/9/update-branch"]


def test_an_unknown_mergeable_is_read_again_until_github_computed_it(tmp_path: Path):
    """The first read gives UNKNOWN, the second CONFLICTING, and the conflict goes back to an implementer. The reads
    stop at READS (the workflow's fixed limit) with a warning and a passed run: the next run reads them again."""
    step = next(s for s in yaml.safe_load(UPDATE.read_text())["jobs"]["update"]["steps"] if s.get("name") == STEP)
    assert step["env"]["READS"] == 6 and step["env"]["WAIT"] == 10, "one minute of reads at most, within the 5 min job"
    conflict = ("pulls/3/update-branch", '{"message":"merge conflict between base and head","status":"422"}', 1)
    early = ("api graphql", json.dumps(reply(pull(3, mergeable="UNKNOWN"))), 0, 1)
    fast = {"READS": "6", "WAIT": "0"}
    run, calls = run_update(tmp_path / "later", [early, conflict], reply(pull(3)), more=fast)
    assert run.returncode == 0, run.stdout + run.stderr
    assert sum(c.startswith("api graphql") for c in calls) == 3, "UNKNOWN, CONFLICTING, then nothing left"
    assert updates(calls) == ["repos/owner/repo/pulls/3/update-branch"] and "pr merge 3 -R owner/repo --disable-auto" in calls
    assert "read again in 0 s" in run.stdout
    run, calls = run_update(tmp_path / "never", [], reply(pull(3, mergeable="UNKNOWN")), more={"READS": "3", "WAIT": "0"})
    assert run.returncode == 0, run.stdout + run.stderr
    assert sum(c.startswith("api graphql") for c in calls) == 3 and updates(calls) == []
    assert "::warning::GitHub has not computed mergeable after 3 reads (pull requests 3)" in run.stdout


def test_a_moved_head_is_skipped_and_an_unknown_refusal_fails_the_run(tmp_path: Path):
    moved = ("pulls/3/update-branch", "gh: expected head sha didn’t match current head ref. (HTTP 422)", 1)
    run, calls = run_update(tmp_path / "moved", [moved], reply(pull(3, auto="2026-10-28T08:00:00Z"), pull(9)))
    assert run.returncode == 0 and updates(calls)[-1] == "repos/owner/repo/pulls/9/update-branch"
    assert not any(c.startswith(("pr comment", "pr merge")) for c in calls), "a push came first: nothing to say"
    other = ("pulls/3/update-branch", "gh: Bad credentials (HTTP 401)", 1)
    run, calls = run_update(tmp_path / "other", [other], reply(pull(3, auto="2026-10-28T08:00:00Z"), pull(9)))
    assert run.returncode == 1 and "::error::" in run.stdout and len(updates(calls)) == 1


def test_each_run_block_of_the_workflow_is_one_call_of_the_script():
    """The logic moved into branch_update.py, so a change of the rules is a change of code, not a workflow patch."""
    jobs = yaml.safe_load(UPDATE.read_text())["jobs"]
    runs = {(name, step["name"]): step["run"].strip() for name, job in jobs.items() for step in job["steps"] if "run" in step}
    assert runs == {("update", STEP): "python3 -I .github/branch_update.py update"}, "the job `failed` is gates_failed.py's now"


def test_the_workflow_runs_mains_code_one_run_at_a_time():
    """On each merge, on each completed run of the review workflow that ran a job (an approve, a carried check or a new
    review may let the queue move), on a passed rerun of the gates (failed checks turn passed with no push), and
    by hand. A first run of the gates starts nothing here (gates-failed.yml reads it), and a review run whose jobs all skipped
    starts nothing (each `update` takes one of the profile's 8 machines).
    The job checks out main only and holds the implementer app's token with no checks write (agents/README.md "The guards")."""
    flow = yaml.safe_load(UPDATE.read_text())
    on = flow[True] if True in flow else flow["on"]
    assert on["push"] == {"branches": ["main"]}
    assert on["workflow_run"] == {"workflows": ["gates", "Claude Code Review"], "types": ["completed"]}
    assert "workflow_dispatch" in on and "pull_request" not in on and "pull_request_target" not in on
    assert flow["jobs"]["update"]["concurrency"] == {"group": "branch-update", "cancel-in-progress": False}
    assert flow["permissions"] == {"contents": "read"}
    for name, file in (("gates", "gates.yml"), ("Claude Code Review", "claude-code-review.yml")):
        assert yaml.safe_load((REAL_REPO / ".github" / "workflows" / file).read_text())["name"] == name, \
            "workflow_run names a workflow by its name"
    job = flow["jobs"]["update"]
    # R1-2: an approve, a carried check, a new review or a passed rerun of the gates can let
    # the queue move: a first gates run and a review run that ran no job cannot, so they start no machine
    assert job["if"] == (
        "vars.IMPLEMENTER_APP_CLIENT_ID != '' && (github.event_name != 'workflow_run' "
        "|| (github.event.workflow_run.path == '.github/workflows/claude-code-review.yml' && github.event.workflow_run.conclusion != 'skipped') "
        "|| (github.event.workflow_run.path == '.github/workflows/gates.yml' && github.event.workflow_run.conclusion == 'success' "
        "&& github.event.workflow_run.run_attempt > 1))"
    )
    checkout = job["steps"][0]
    assert checkout["uses"].startswith("actions/checkout@") and checkout["with"]["ref"] == "${{ github.event.repository.default_branch }}"
    token = next(s for s in job["steps"] if s.get("id") == "token")
    assert token["with"]["client-id"] == "${{ vars.IMPLEMENTER_APP_CLIENT_ID }}"
    assert token["with"]["permission-checks"] == "read" and token["with"]["permission-contents"] == "write"


# The two jobs as functions, with `gh`, linear.py and the wait faked. Each case names a branch of the logic that the
# workflow had as shell; the tests above run the workflow's step with the same fakes.
class Tools:
    """Fakes `gh` and linear.py: a call is answered by the first rule whose text is in the call, else with success."""

    def __init__(self, monkeypatch, rules: list[tuple] | None = None) -> None:
        """A rule is (text, code, output) or (text, code, output, times): the last answers its first `times` calls only."""
        self.calls: list[str] = []
        self.used: dict[int, int] = {}
        self.waits: list[float] = []
        self.rules = rules or []
        monkeypatch.setattr(update, "gh", lambda *a: self.answer("gh", a))
        monkeypatch.setattr(update, "linear", lambda *a: self.answer("linear", a))
        monkeypatch.setattr(update, "sleep", self.waits.append)

    def answer(self, tool: str, args: tuple[str, ...]) -> update.Result:
        call = " ".join(args)
        self.calls.append(call if tool == "gh" else f"linear {call}")
        for n, (part, code, out, *times) in enumerate(self.rules):
            if part in call and (not times or self.used.get(n, 0) < times[0]):
                self.used[n] = self.used.get(n, 0) + 1
                return update.Result(code, out, "")
        return update.Result(0, "", "")


ENV = {"GITHUB_REPOSITORY": "owner/repo", "LINEAR_API_KEY": "key", "READS": "3", "WAIT": "7"}
CONFLICT = ("pulls/3/update-branch", 1, '{"message":"merge conflict between base and head","status":"422"}')
SESSION = [("--json headRefName", 0, "abc-1239-x\n"), ("--json title", 0, "A title\n")]


def graphql(*pulls: dict) -> tuple[str, int, str]:
    return ("api graphql", 0, json.dumps(reply(*pulls)))


def only(calls: list[str], start: str) -> list[str]:
    return [c for c in calls if c.startswith(start)]


def test_the_job_update_asks_github_to_merge_main_into_the_oldest_conflicting_pull_request(monkeypatch, capsys):
    tools = Tools(monkeypatch, [graphql(pull(9), pull(3, auto="2026-10-28T08:00:00Z"))])
    assert update.update(ENV) == 0
    assert f"api -X PUT repos/owner/repo/pulls/3/update-branch -f expected_head_sha={3:040d}" in tools.calls
    assert len(only(tools.calls, "api -X PUT")) == 1, "one update per run"
    assert "::notice::PR #3: GitHub merges main into the branch" in capsys.readouterr().out
    tools = Tools(monkeypatch, [graphql(pull(9, behind=0))])
    assert update.update(ENV) == 0 and not only(tools.calls, "api -X PUT")
    assert "no pull request conflicts with main" in capsys.readouterr().out


def test_a_conflict_gets_one_comment_auto_merge_off_the_issue_in_progress_and_the_next_pull_request_its_update(monkeypatch, capsys):
    tools = Tools(monkeypatch, [CONFLICT, *SESSION, ("key-from", 0, "ABC-9\n"), ("issues/3/comments", 0, "An older comment\n"),
                                graphql(pull(9), pull(3, auto="2026-10-28T08:00:00Z"))])
    assert update.update(ENV) == 0
    calls = tools.calls
    comment = only(calls, "pr comment 3")
    assert len(comment) == 1
    body = comment[0].split("--body ", 1)[1]
    assert body.startswith(verdict.CONFLICT) and f"Its head is {3:040d}." in body and "Auto-merge is off until the next approve." in body
    assert "round 1" in body
    disable = calls.index("pr merge 3 -R owner/repo --disable-auto")
    assert calls.index(comment[0]) < disable < calls.index(f"api -X PUT repos/owner/repo/pulls/9/update-branch -f expected_head_sha={9:040d}")
    assert only(calls, "linear") == ["linear key-from abc-1239-x A title", "linear state ABC-9 In Progress"]


def test_a_conflict_on_a_pull_request_that_waits_for_the_owners_merge_loses_the_label_and_has_no_auto_merge_to_turn_off(monkeypatch):
    tools = Tools(monkeypatch, [CONFLICT, *SESSION, ("key-from", 0, "ABC-9\n"), graphql(owners(3))])
    assert update.update(ENV) == 0
    assert "api -X DELETE repos/owner/repo/issues/3/labels/escalated" in tools.calls
    assert not only(tools.calls, "pr merge")
    body = only(tools.calls, "pr comment 3")[0]
    assert "Auto-merge" not in body and verdict.CONFLICT in body
    assert only(tools.calls, "linear state") == ["linear state ABC-9 In Progress"]


def test_a_head_that_has_the_comment_already_gets_no_second_one_and_goes_on(monkeypatch, capsys):
    said = f"An older comment\n{verdict.CONFLICT}\n"
    words = f"The branch update found a merge conflict with main. Its head is {3:040d}."
    tools = Tools(monkeypatch, [CONFLICT, *SESSION, ("key-from", 0, "ABC-9\n"), ("issues/3/comments", 0, said + words + "\n"),
                                graphql(pull(3))])
    assert update.update(ENV) == 0
    assert not only(tools.calls, "pr comment"), "said already"
    assert "pr merge 3 -R owner/repo --disable-auto" in tools.calls and "linear state ABC-9 In Progress" in tools.calls
    assert "has its comment already" in capsys.readouterr().out
    # the older words of a pull request that waited for the owner's merge name the head too, but they are not these words
    older = f"This pull request waits for the owner's merge, and the branch update found a merge conflict with its head {3:040d}.\n"
    tools = Tools(monkeypatch, [CONFLICT, *SESSION, ("issues/3/comments", 0, older), graphql(pull(3))])
    update.update(ENV)
    assert len(only(tools.calls, "pr comment 3")) == 1
    # a list of comments that GitHub did not give is no reason to stay silent
    tools = Tools(monkeypatch, [CONFLICT, *SESSION, ("issues/3/comments", 1, ""), graphql(pull(3))])
    update.update(ENV)
    assert len(only(tools.calls, "pr comment 3")) == 1


def test_a_failed_comment_changes_nothing_else_and_the_next_pull_request_still_gets_its_update(monkeypatch, capsys):
    tools = Tools(monkeypatch, [CONFLICT, ("pr comment", 1, ""), graphql(pull(9), pull(3, auto="2026-10-28T08:00:00Z"))])
    assert update.update(ENV) == 0
    assert not only(tools.calls, "pr merge") and not only(tools.calls, "linear") and not only(tools.calls, "api -X DELETE")
    assert [c.split()[3] for c in only(tools.calls, "api -X PUT")] == ["repos/owner/repo/pulls/3/update-branch", "repos/owner/repo/pulls/9/update-branch"]
    out = capsys.readouterr().out
    assert "::warning::PR #3: the comment on the conflict failed" in out and "left as it is, for the next run" in out
    tools = Tools(monkeypatch, [CONFLICT, ("pr comment", 1, ""), ("issues/3/comments", 0, ""), graphql(owners(3), owners(9))])
    update.update(ENV)
    assert not only(tools.calls, "api -X DELETE") and not only(tools.calls, "linear"), "the label stays: the queue keeps it"


def test_a_failed_label_removal_or_auto_merge_switch_only_warns_and_the_issue_still_moves(monkeypatch, capsys):
    tools = Tools(monkeypatch, [CONFLICT, *SESSION, ("key-from", 0, "ABC-9\n"), ("api -X DELETE", 1, ""), graphql(owners(3))])
    assert update.update(ENV) == 0
    assert "::warning::PR #3: the label escalated could not be removed: remove it by hand" in capsys.readouterr().out
    assert "linear state ABC-9 In Progress" in tools.calls
    tools = Tools(monkeypatch, [CONFLICT, *SESSION, ("key-from", 0, "ABC-9\n"), ("pr merge", 1, ""), graphql(pull(3))])
    assert update.update(ENV) == 0
    assert "::warning::PR #3: auto-merge could not be turned off" in capsys.readouterr().out
    assert "linear state ABC-9 In Progress" in tools.calls


def test_a_pull_request_that_gh_cannot_view_still_goes_back_and_its_issue_has_no_key_to_move(monkeypatch):
    """The branch and the title are read after the comment: when gh fails there, the texts are empty, key-from finds no key,
    and the conflict still has its comment and its auto-merge off (the shell's `|| branch=""` and `|| title=""`)."""
    tools = Tools(monkeypatch, [CONFLICT, ("pr view", 1, ""), graphql(pull(3))])
    assert update.update(ENV) == 0
    assert only(tools.calls, "pr comment 3") and "pr merge 3 -R owner/repo --disable-auto" in tools.calls
    assert only(tools.calls, "linear") == ["linear key-from  "] and not only(tools.calls, "linear state")


def test_the_issue_moves_only_with_a_key_and_a_linear_that_answers(monkeypatch, capsys):
    rules = [CONFLICT, *SESSION, ("key-from", 0, "ABC-9\n"), graphql(pull(3))]
    tools = Tools(monkeypatch, rules)
    update.update({**ENV, "LINEAR_API_KEY": ""})
    assert not only(tools.calls, "linear state"), "no Linear key: no move"
    tools = Tools(monkeypatch, [("key-from", 0, "\n"), *rules])
    update.update(ENV)
    assert not only(tools.calls, "linear state"), "no issue key in the branch or the title"
    tools = Tools(monkeypatch, [("state ABC-9", 1, ""), *rules])
    update.update(ENV)
    assert "::warning::the move of ABC-9 to In Progress failed: move it by hand" in capsys.readouterr().out


def test_an_unknown_mergeable_is_read_again_up_to_reads_times(monkeypatch, capsys):
    tools = Tools(monkeypatch, [graphql(pull(3, mergeable="UNKNOWN"))])
    assert update.update(ENV) == 0
    assert len(only(tools.calls, "api graphql")) == 3 and tools.waits == [7.0, 7.0] and not only(tools.calls, "api -X PUT")
    out = capsys.readouterr().out
    assert "read again in 7 s" in out and "::warning::GitHub has not computed mergeable after 3 reads (pull requests 3)" in out
    early = ("api graphql", 0, json.dumps(reply(pull(3, mergeable="UNKNOWN"))), 1)
    tools = Tools(monkeypatch, [early, CONFLICT, *SESSION, graphql(pull(3))])
    assert update.update(ENV) == 0 and tools.waits == [7.0], "UNKNOWN once, then the conflict"
    assert only(tools.calls, "pr comment 3")


def test_a_moved_head_is_skipped_and_an_unknown_refusal_fails_the_job(monkeypatch, capsys):
    pulls = graphql(pull(3, auto="2026-10-28T08:00:00Z"), pull(9))
    tools = Tools(monkeypatch, [("pulls/3/update-branch", 1, "gh: expected head sha didn’t match current head ref. (HTTP 422)"), pulls])
    assert update.update(ENV) == 0
    assert only(tools.calls, "api -X PUT")[-1].split()[3] == "repos/owner/repo/pulls/9/update-branch"
    assert not only(tools.calls, "pr comment") and not only(tools.calls, "pr merge")
    tools = Tools(monkeypatch, [("pulls/3/update-branch", 1, "gh: Bad credentials (HTTP 401)"), pulls])
    assert update.update(ENV) == 1 and len(only(tools.calls, "api -X PUT")) == 1
    assert "::error::PR #3: GitHub refused the branch update for a reason this job does not know" in capsys.readouterr().out
    tools = Tools(monkeypatch, [("api graphql", 1, "")])
    assert update.update(ENV) == 1, "a failed read fails the job"


def test_the_stderr_of_a_call_reaches_the_log_so_a_failed_write_shows_its_reason(monkeypatch, tmp_path: Path, capsys):
    """The shell left the stderr of `gh` and linear.py in the job's log, so a failed comment shows
    gh's own words (a 403, a rate limit) beside the warning. `run` copies it to the log for every call; the result keeps it
    for `refusal`."""
    done = update.run([sys.executable, "-c", "import sys; print('out'); sys.exit('boom')"])
    assert (done.code, done.out, done.err) == (1, "out\n", "boom\n")
    assert capsys.readouterr().err == "boom\n"
    # the whole path: a fake gh on PATH that fails the comment with a reason
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "gh").write_text("#!/bin/sh\ncase \"$*\" in\n  'pr comment'*) echo 'HTTP 403: rate limit' >&2; exit 1 ;;\nesac\n")
    (bin_ / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_}:/usr/bin:/bin")
    monkeypatch.setattr(update, "linear", lambda *a: update.Result(0, "", ""))
    assert update.conflict("owner/repo", "7", "a" * 40, False, ENV) is False
    seen = capsys.readouterr()
    assert "HTTP 403: rate limit" in seen.err and "::warning::PR #7: the comment on the conflict failed" in seen.out


def test_the_subcommands_read_the_environment(monkeypatch):
    seen = []
    monkeypatch.setattr(update, "update", lambda env: seen.append(("update", env)) or 0)
    assert update.main(["update"]) == 0 and update.main(["failed"]) == 2 and update.main(["update", "x"]) == 2
    assert [name for name, _ in seen] == ["update"] and seen[0][1] is update.os.environ, "`failed` is gates_failed.py's"


def test_no_workflow_matches_a_run_by_its_name() -> None:
    """OWL-83: `claude-code-review.yml` sets `run-name`, so GitHub gives a run's `name` as the pull request's title and a
    condition on `workflow_run.name` is never true. Every workflow matches a run by `workflow_run.path`."""
    for file in sorted((REAL_REPO / ".github" / "workflows").glob("*.yml")):
        assert "workflow_run.name" not in file.read_text(), file.name
    text = UPDATE.read_text()
    assert "workflow_run.path" in text and "run-name" in text, "the top comment says why the path is used"
