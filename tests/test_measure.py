"""The skill `measure`: the pure parts of .agents/skills/measure/measure.py on invented pull requests and issues.
No network: nothing here calls gh."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import REAL_REPO

SPEC = importlib.util.spec_from_file_location("measure", REAL_REPO / ".agents" / "skills" / "measure" / "measure.py")
assert SPEC and SPEC.loader
measure = importlib.util.module_from_spec(SPEC)
sys.modules["measure"] = measure
SPEC.loader.exec_module(measure)


REVIEWER_LOGIN = "some-owner" + measure.REVIEWER  # the reviewer app's login


def review(verdict: str, round_: int, at: str, findings: list[dict] | None = None, state: str = "COMMENTED",
           author: str = REVIEWER_LOGIN) -> dict:
    body = "Text.\n\n```json\n" + json.dumps({"verdict": verdict, "round": round_, "findings": findings or []}) + "\n```"
    return {"author": {"login": author}, "state": state, "submittedAt": at, "body": body}


def pr(number: int, created: str, merged: str, *, ready: list[str] = (), reviews: list[dict] = (), labels: list[tuple] = (),
       merged_by: str = "implementer", additions: int = 10, deletions: int = 0, branch: str = "", checks: list[dict] = (),
       comments: list[str] = (), commits: list[str] = ()) -> dict:
    timeline = [{"__typename": "ReadyForReviewEvent", "createdAt": r} for r in ready]
    timeline += [{"__typename": kind, "createdAt": at, "label": {"name": name}} for kind, name, at in labels]
    return {"number": number, "title": f"PR {number}", "createdAt": created, "mergedAt": merged, "headRefName": branch,
            "mergedBy": {"login": merged_by}, "additions": additions, "deletions": deletions,
            "timelineItems": {"nodes": timeline}, "reviews": {"nodes": list(reviews)},
            "comments": {"nodes": [{"createdAt": c} for c in comments]},
            "commits": {"nodes": [{"commit": {"committedDate": c, "checkSuites": {"nodes": [{"checkRuns": {"nodes": list(checks)}}]}}}
                                  for c in (commits or [created])]}}


def test_lead_time_starts_at_the_first_ready_mark_or_the_creation():
    opened_ready = pr(1, "2026-11-01T10:00:00Z", "2026-11-01T10:30:00Z")
    draft = pr(2, "2026-11-01T09:00:00Z", "2026-11-01T12:00:00Z", ready=["2026-11-01T11:30:00Z", "2026-11-01T11:00:00Z"])
    assert measure.first_ready(opened_ready) == "2026-11-01T10:00:00Z"
    assert measure.first_ready(draft) == "2026-11-01T11:00:00Z", "the first mark, whatever the order"
    assert measure.median_p90([30, 60, None]) == (45, 60, 2)
    assert measure.median_p90([]) is None
    assert measure.median_p90(range(1, 11)) == (5.5, 10, 10), "rank 0.9 of ten values is the tenth"


def test_the_active_share_counts_gaps_of_30_minutes_or_less():
    stamps = ["2026-11-01T10:10:00Z", "2026-11-01T10:20:00Z", "2026-11-01T12:00:00Z", "2026-11-01T09:00:00Z"]
    active, total = measure.active_share(stamps, "2026-11-01T10:00:00Z", "2026-11-01T12:30:00Z")
    # gaps: 10, 10, 100 (idle), 30 (active: 30 is not more than 30); 09:00 is before the start
    assert (active, total) == (50, 150)
    assert measure.active_share([], "2026-11-01T10:00:00Z", "2026-11-01T10:20:00Z") == (20, 20)


def test_verdicts_are_the_reviewers_reviews_with_a_verdict_json():
    p = pr(1, "2026-11-01T10:00:00Z", "2026-11-01T11:00:00Z", reviews=[
        review("request-changes", 1, "2026-11-01T10:10:00Z", [{"fix_now": True}]),
        {"author": {"login": REVIEWER_LOGIN}, "state": "COMMENTED", "submittedAt": "2026-11-01T10:11:00Z", "body": "an inline note"},
        review("approve", 1, "2026-11-01T10:12:00Z", author="someone"),
        {"author": {"login": REVIEWER_LOGIN}, "state": "COMMENTED", "submittedAt": "x", "body": "```json\n{not json\n```"},
        review("approve", 2, "2026-11-01T10:40:00Z", state="APPROVED")])
    assert [(v["verdict"], v["round"]) for v in measure.verdicts(p)] == [("request-changes", 1), ("approve", 2)]
    assert measure.fix_now_only({"findings": [{"fix_now": True}, {"blocking": False}]})
    assert not measure.fix_now_only({"findings": [{"fix_now": True}, {"blocking": True}]})
    assert measure.blocking({"findings": [{"blocking": True}]}) and not measure.blocking({"findings": []})


def test_the_size_bands():
    assert [measure.size_band(n) for n in (0, 100, 101, 400, 401)] == ["100 or fewer", "100 or fewer", "101 to 400", "101 to 400", "over 400"]


def test_the_escalated_dwell_ends_at_the_unlabel_or_the_merge():
    p = pr(1, "2026-11-01T10:00:00Z", "2026-11-01T14:00:00Z", labels=[
        ("LabeledEvent", "escalated", "2026-11-01T10:00:00Z"), ("UnlabeledEvent", "escalated", "2026-11-01T11:00:00Z"),
        ("LabeledEvent", "workflow", "2026-11-01T11:30:00Z"), ("LabeledEvent", "escalated", "2026-11-01T13:00:00Z")])
    assert measure.escalated_minutes(p) == 120


def test_the_loop_numbers_on_three_pull_requests():
    rc = review("request-changes", 1, "2026-11-01T10:10:00Z", [{"fix_now": True, "file": "a.md"}, {"fix_now": True, "file": "a.py"}])
    a = pr(1, "2026-11-01T10:00:00Z", "2026-11-01T11:00:00Z", additions=50,
           reviews=[rc, review("approve", 2, "2026-11-01T10:40:00Z", state="APPROVED")],
           checks=[{"name": "python", "conclusion": "SUCCESS", "startedAt": "2026-11-01T10:00:00Z", "completedAt": "2026-11-01T10:02:00Z"},
                   {"name": "web", "conclusion": "FAILURE", "startedAt": "2026-11-01T10:00:00Z", "completedAt": "2026-11-01T10:09:00Z"}])
    # the owner's own approve right before the merge is not the start of the owner's wait
    b = pr(2, "2026-11-01T10:00:00Z", "2026-11-01T12:00:00Z", additions=300, deletions=200, merged_by="owner",
           reviews=[review("approve", 1, "2026-11-01T10:05:00Z", state="APPROVED"),
                    {"author": {"login": "owner"}, "state": "APPROVED", "submittedAt": "2026-11-01T11:59:00Z", "body": ""}])
    c = pr(3, "2026-11-01T10:00:00Z", "2026-11-01T10:20:00Z", additions=120,
           reviews=[review("request-changes", 1, "2026-11-01T10:05:00Z", [{"blocking": True}]),
                    review("request-changes", 2, "2026-11-01T10:15:00Z", [{"blocking": True}])])
    rows = dict(measure.loop_numbers([a, b, c], "owner"))
    assert rows["pull requests"] == "3"
    assert rows["lead time, ready mark to merge"] == "60.0 min median, p90 120.0 (n 3)"
    assert rows["verdicts per pull request"] == "1.67"
    assert rows["round 1 approve"] == "33% (1 of 3)" and rows["round 1 request-changes"] == "67% (2 of 3)"
    assert rows["round 1 request-changes with fix_now findings only"] == "50% (1 of 2)"
    assert rows["blocking rounds per pull request"] == "0.67"
    assert rows["round 2 approve after a round-1 request-changes"] == "50% (1 of 2)"
    assert rows["still blocked after round 2"] == "1" and rows["still blocked after round 3"] == "0"
    assert rows["merges by the owner"] == "33% (1 of 3)"
    # the owner's wait: 10:05 to 12:00 = 115 min, of 60 + 120 + 20 = 200 min ready to merge
    assert rows["the owner's wait after the last approve"].startswith("2 h in all, 58% of the ready-to-merge time")
    assert rows["check python"] == "2.0 min median, p90 2.0 (n 1)" and rows["check web"] == "none"
    assert rows["last green gate to merge"] == "58.0 min median, p90 58.0 (n 1)"
    assert rows["round 1 approve, 100 or fewer lines"] == "0% (0 of 1)"
    assert rows["round 1 approve, 101 to 400 lines"] == "0% (0 of 1)" and rows["round 1 approve, over 400 lines"] == "100% (1 of 1)"
    assert rows["round 1 fix_now findings in a document"] == "50% (1 of 2)"


def test_rounds_come_from_the_verdict_json_not_the_position():
    """The count starts again on an approved pull request, so a second verdict can be a new round 1.
    Approve (1), request-changes (1), approve (2) is no pull request still blocked after round 2."""
    restarted = pr(1, "2026-11-01T10:00:00Z", "2026-11-01T12:00:00Z", branch="abc-7-a", reviews=[
        review("approve", 1, "2026-11-01T10:05:00Z"), review("request-changes", 1, "2026-11-01T10:50:00Z"),
        review("approve", 2, "2026-11-01T11:30:00Z")])
    rows = dict(measure.loop_numbers([restarted], "owner"))
    assert rows["still blocked after round 2"] == "0"
    assert rows["round 2 approve after a round-1 request-changes"] == "100% (1 of 1)"
    table = measure.by_run([restarted], [{"identifier": "ABC-7", "comments": [{"created": "x", "body": "Run: m, effort low"}]}])
    assert table[("m", "low", "none")]["escalated after the last round"] == 0 and table[("m", "low", "none")]["review rounds"] == 3


def test_a_pull_request_goes_to_the_owner_after_its_last_round():
    """Three rounds at most since 2026-11-16. A round 2 that asks for changes is no longer the last; a round-2 or
    round-3 needs-decision is (before the change, round 2's block was a needs-decision)."""
    third = [review("request-changes", 1, "2026-11-01T10:05:00Z", [{"blocking": True}]), review("request-changes", 2, "2026-11-01T10:30:00Z", [{"blocking": True}]),
             review("needs-decision", 3, "2026-11-01T11:00:00Z", [{"blocking": True}])]
    assert measure.escalated_after_the_last([dict(round=v, verdict=w) for v, w in ((1, "request-changes"), (2, "request-changes"))]) is False
    blocked = [{"blocking": True}]
    assert measure.escalated_after_the_last([{"round": 2, "verdict": "needs-decision", "findings": blocked}]) is True, "the two-round era"
    assert measure.escalated_after_the_last([{"round": 3, "verdict": "needs-decision", "findings": blocked}]) is True
    assert measure.escalated_after_the_last([{"round": 2, "verdict": "needs-decision"}]) is False, "A step before the merge, nothing blocked"
    assert measure.escalated_after_the_last([{"round": 3, "verdict": "needs-decision", "findings": [{"blocking": False}]}]) is False
    open_previous = {"id": "R1-1", "fixed": False}
    assert measure.escalated_after_the_last([{"round": 3, "verdict": "needs-decision", "findings": [], "previous": [open_previous]}]) is True
    assert measure.escalated_after_the_last([{"round": 3, "verdict": "needs-decision", "findings": [], "previous": [{**open_previous, "fix_now": True}]}]) is False
    assert measure.escalated_after_the_last([{"round": 3, "verdict": "needs-decision", "findings": [], "previous": [{"id": "R1-1", "fixed": True}]}]) is False
    rows = dict(measure.loop_numbers([pr(1, "2026-11-01T10:00:00Z", "2026-11-01T12:00:00Z", reviews=third)], "owner"))
    assert rows["still blocked after round 2"] == "1" and rows["still blocked after round 3"] == "1"


def test_red_main_counts_failed_runs_over_ended_ones():
    runs = [{"conclusion": "success"}, {"conclusion": "failure"}, {"conclusion": "cancelled"}, {"conclusion": "success"}]
    assert measure.red_main(runs) == ("red gates runs on main", "33% (1 of 3)")


def test_mains_runs_are_read_from_each_event_old_push_runs_too():
    """Main's gates run once each night and on demand; a window before 2026-11-12 holds push runs."""
    replies = {"push": [{"conclusion": "failure", "createdAt": "2026-11-10T01:00:00Z"}],
               "schedule": [{"conclusion": "success", "createdAt": "2026-11-13T02:47:00Z"},
                            {"conclusion": "success", "createdAt": "2026-11-20T02:47:00Z"}],
               "workflow_dispatch": [{"conclusion": "success", "createdAt": "2026-11-14T09:00:00Z"}]}
    calls: list[tuple[str, ...]] = []

    def gh(*args: str) -> str:
        calls.append(args)
        return json.dumps(replies[args[args.index("--event") + 1]])
    runs = measure.main_runs("o/r", "2026-11-08", "2026-11-20", gh)
    assert [a[a.index("--event") + 1] for a in calls] == ["push", "schedule", "workflow_dispatch"]
    assert all(("--branch", "main") == a[a.index("--branch"):a.index("--branch") + 2] for a in calls)
    assert all("2026-11-08..2026-11-20" in a for a in calls)
    assert [r["createdAt"][:10] for r in runs] == ["2026-11-10", "2026-11-13", "2026-11-14"], "the --until day is left out"
    assert measure.red_main(runs) == ("red gates runs on main", "33% (1 of 3)")


def test_a_team_with_no_gates_workflow_gets_none_and_the_report_goes_on():
    """`gh run list --workflow gates.yml` exits 1 for a repository without it; any other failure still stops."""
    def missing(*args: str) -> str:
        raise subprocess.CalledProcessError(1, ["gh", *args], stderr="workflow gates.yml not found on the default branch")

    def broken(*args: str) -> str:
        raise subprocess.CalledProcessError(1, ["gh", *args], stderr="HTTP 502")
    runs = measure.main_runs("o/tools", "2026-11-08", "2026-11-20", missing)
    assert measure.red_main(runs) == ("red gates runs on main", "none")
    with pytest.raises(subprocess.CalledProcessError):
        measure.main_runs("o/r", "2026-11-08", "2026-11-20", broken)


def test_follow_up_and_fast_lane_issues_in_the_window():
    """A follow-up issue collects several reviews' findings: it counts once, and its checklist lines count apart."""
    two = "From: PR #1, round 1, a, depth 1\nFrom: PR #2, round 1, b, depth 1\n\n## From PR #1\n\n- [ ] R1-1 a\n- [x] R1-2 b\n" \
          "\n## From PR #2\n\n- [ ] R1-1 c\n"
    issues = [{"identifier": "ABC-1", "labels": ["follow-up"], "state": "Done", "created": "2026-11-01T00:00:00Z", "description": two},
              {"identifier": "ABC-2", "labels": ["follow-up", "fast-lane"], "state": "Canceled", "created": "2026-11-02T00:00:00Z"},
              {"identifier": "ABC-3", "labels": ["fast-lane"], "state": "Todo", "created": "2026-11-09T00:00:00Z"}]
    assert measure.issue_numbers(issues, "2026-10-29", "2026-11-09") == [
        ("follow-up issues filed", "2, Done 1, Canceled 1, findings 3"), ("fast-lane issues filed", "1, Done 0, Canceled 1")]


def test_the_first_run_line_groups_the_issue():
    comments = [{"created": "2026-11-02T00:00:00Z", "body": "Run: sonnet-5-5, effort medium (fix round of PR #3)"},
                {"created": "2026-11-01T00:00:00Z", "body": "A comment.\n\nRun: claude-opus-5-5, effort high"}]
    assert measure.first_run(comments) == ("claude-opus-5-5", "high", "none")
    assert measure.first_run([{"created": "x", "body": "Run: m, effort unknown"}]) == ("m", "unknown", "none")
    assert measure.first_run([{"created": "x", "body": "The run failed."}]) is None
    issues = [{"identifier": "ABC-7", "comments": comments}, {"identifier": "ABC-8", "comments": [{"created": "x", "body": "Run: m, effort low"}]},
              {"identifier": "ABC-9", "comments": []}]
    prs = [pr(1, "2026-11-01T10:00:00Z", "2026-11-01T11:00:00Z", branch="abc-7-a", reviews=[
               review("request-changes", 1, "2026-11-01T10:05:00Z", [{"blocking": True}]),
               review("needs-decision", 2, "2026-11-01T10:30:00Z", [{"blocking": True}])]),
           pr(2, "2026-11-01T10:00:00Z", "2026-11-01T11:00:00Z", branch="abc-7-a-2", reviews=[review("approve", 1, "2026-11-01T10:05:00Z")]),
           pr(3, "2026-11-01T10:00:00Z", "2026-11-01T11:00:00Z", branch="ABC-8-b"),
           pr(4, "2026-11-01T10:00:00Z", "2026-11-01T11:00:00Z", branch="abc-9-c")]
    table = measure.by_run(prs, issues)
    assert table[("claude-opus-5-5", "high", "none")] == {"issues": 1, "pull requests": 2, "review rounds": 3, "blocking rounds": 2,
                                                  "escalated after the last round": 1}
    assert table[("m", "low", "none")]["pull requests"] == 1 and len(table) == 2, "an issue with no run line is in no group"


def test_the_local_review_s_model_groups_the_issues_beside_the_run_s_model():
    """The run line names the local review's model and effort level too, so `measure` reads which review model found
    the faults. The run's model stays the first table's group, and a line of before the review's settings (or a run started without the
    settings) is in no review group, never in one named `unknown`."""
    comments = [{"created": "2026-11-02T00:00:00Z",
                 "body": "Run: claude-sonnet-5-5, effort medium; review deepseek-v4.1-flash, effort high (fix round of PR #3)"},
                {"created": "2026-11-01T00:00:00Z",
                 "body": "Run: claude-opus-5-5, effort high; review claude-sonnet-5-5, effort high\n"}]
    assert measure.first_run(comments) == ("claude-opus-5-5", "high", "none"), "the run's model, as the first run line gives it"
    assert measure.first_review(comments) == ("claude-sonnet-5-5", "high"), "the first line that names one, by the date"
    assert measure.first_review([{"created": "x", "body": "Run: m, effort low"}]) is None, "a line of before the review's settings"
    assert measure.first_review([{"created": "x", "body": "Run: m, effort low; review n, effort none"}]) == ("n", "none")
    assert measure.first_review([{"created": "x", "body": "The run failed."}]) is None
    issues = [{"identifier": "ABC-7", "comments": comments},
              {"identifier": "ABC-8", "comments": [{"created": "x", "body": "Run: glm-5.3, effort low; review n, effort medium"}]},
              {"identifier": "ABC-9", "comments": [{"created": "x", "body": "Run: o, effort low"}]}]
    prs = [pr(1, "2026-11-01T10:00:00Z", "2026-11-01T11:00:00Z", branch="abc-7-a"),
           pr(2, "2026-11-01T10:00:00Z", "2026-11-01T11:00:00Z", branch="abc-8-b"),
           pr(3, "2026-11-01T10:00:00Z", "2026-11-01T11:00:00Z", branch="abc-9-c")]
    reviews = measure.by_run(prs, issues, key=measure.first_review)
    assert set(reviews) == {("claude-sonnet-5-5", "high"), ("n", "medium")}
    assert reviews[("n", "medium")]["pull requests"] == 1 and sum(r["issues"] for r in reviews.values()) == 2
    assert set(measure.by_run(prs, issues)) == {("claude-opus-5-5", "high", "none"), ("glm-5.3", "low", "none"), ("o", "low", "none")}, "as before"


def test_the_run_line_s_profile_groups_the_issue_beside_its_model():
    """A cell can run on another profile than the one in force, so the run line names the profile after the
    review (`; profile NAME`). The table by model and effort groups by it; an older line, with no profile, is in "none"."""
    mixed = {"created": "x", "body": "Run: claude-opus-5-5, effort high; review pi-lithosai/zai-org/GLM-5.3-Flash, effort max; "
                                    "profile claude (start 20261123T112829Z-mini)"}
    fix = {"created": "x", "body": "Run: m, effort high; review r, effort max; profile pi-lithosai (fix round of PR #7)"}
    older = {"created": "x", "body": "Run: claude-opus-5-5, effort high; review claude-sonnet-5-5, effort high (start 20261123T112829Z-mini)"}
    assert measure.first_run([mixed]) == ("claude-opus-5-5", "high", "claude")
    assert measure.first_review([mixed]) == ("pi-lithosai/zai-org/GLM-5.3-Flash", "max"), "the profile is not part of the effort"
    assert measure.first_run([fix]) == ("m", "high", "pi-lithosai") and measure.first_review([fix]) == ("r", "max")
    assert measure.first_run([older]) == ("claude-opus-5-5", "high", "none")
    issues = [{"identifier": "ABC-7", "comments": [mixed]}, {"identifier": "ABC-8", "comments": [older]}]
    prs = [pr(1, "2026-11-01T10:00:00Z", "2026-11-01T11:00:00Z", branch="abc-7-a"),
           pr(2, "2026-11-01T10:00:00Z", "2026-11-01T11:00:00Z", branch="abc-8-b")]
    assert set(measure.by_run(prs, issues)) == {("claude-opus-5-5", "high", "claude"), ("claude-opus-5-5", "high", "none")}


def test_old_and_new_pi_review_names_group_together():
    """A window can hold an older pi run line with the review's plain catalog ID and a newer one with the
    provider prefix that a later change added. Both group under the full name that pi got, also when the catalog ID holds a
    slash and is no provider. A Claude Code line keeps its names."""
    old = {"created": "2026-11-02T00:00:00Z",
           "body": "Run: pi-lithosai/deepseek-ai/DeepSeek-V4.1-Flash, effort high; review zai-org/GLM-5.3-Flash, effort max"}
    new = {"created": "2026-11-03T00:00:00Z",
           "body": "Run: pi-lithosai/deepseek-ai/DeepSeek-V4.1-Flash, effort high; "
                   "review pi-lithosai/zai-org/GLM-5.3-Flash, effort max"}
    assert measure.first_review([old]) == ("pi-lithosai/zai-org/GLM-5.3-Flash", "max"), "the run's provider is added"
    assert measure.first_review([new]) == ("pi-lithosai/zai-org/GLM-5.3-Flash", "max"), "a prefix is never added twice"
    bare = {"created": "x", "body": "Run: pi-ollama/glm-5.3, effort low; review pi-ollama, effort high"}
    assert measure.first_review([bare]) == ("pi-ollama", "high"), "a bare prefix is not prefixed again"
    claude = {"created": "x", "body": "Run: claude-opus-5-5, effort high; review claude-sonnet-5-5, effort high"}
    assert measure.first_review([claude]) == ("claude-sonnet-5-5", "high"), "a Claude Code line keeps its names"
    plain = {"created": "x", "body": "Run: claude-opus-5-5, effort high; review zai-org/GLM-5.3-Flash, effort high"}
    assert measure.first_review([plain]) == ("zai-org/GLM-5.3-Flash", "high"), "the review's slash is no provider"
    issues = [{"identifier": "ABC-7", "comments": [old]}, {"identifier": "ABC-8", "comments": [new]},
              {"identifier": "ABC-9", "comments": [claude]}]
    prs = [pr(1, "2026-11-01T10:00:00Z", "2026-11-01T11:00:00Z", branch="abc-7-a"),
           pr(2, "2026-11-01T10:00:00Z", "2026-11-01T11:00:00Z", branch="abc-8-b"),
           pr(3, "2026-11-01T10:00:00Z", "2026-11-01T11:00:00Z", branch="abc-9-c")]
    reviews = measure.by_run(prs, issues, key=measure.first_review)
    assert set(reviews) == {("pi-lithosai/zai-org/GLM-5.3-Flash", "max"), ("claude-sonnet-5-5", "high")}
    assert reviews[("pi-lithosai/zai-org/GLM-5.3-Flash", "max")]["issues"] == 2, "old and new lines in one group"


def test_each_teams_keys_find_their_issues():
    """One Linear team per repository. A pull request of another repository names its own key, and the table by model and
    effort groups it under that issue's run line, beside the product's ABC keys."""
    assert measure.issue_key({"headRefName": "xyz-2-privileged", "title": ""}, ("ABC", "XYZ")) == "XYZ-2"
    assert measure.issue_key({"headRefName": "xyz-2-privileged", "title": ""}, ("ABC",)) is None, "another team's key"
    assert measure.issue_key({"headRefName": "x", "title": "ABC-9: a title"}, ("ABC",)) == "ABC-9"
    issues = [{"identifier": "ABC-7", "comments": [{"created": "x", "body": "Run: m, effort low"}]},
              {"identifier": "XYZ-7", "comments": [{"created": "x", "body": "Run: n, effort high"}]}]
    prs = [pr(1, "2026-11-01T10:00:00Z", "2026-11-01T11:00:00Z", branch="abc-7-a"),
           pr(1, "2026-11-01T10:00:00Z", "2026-11-01T11:00:00Z", branch="xyz-7-a")]
    table = measure.by_run(prs, issues)
    assert table[("m", "low", "none")]["issues"] == 1 and table[("n", "high", "none")]["issues"] == 1


def test_the_total_keeps_two_repositories_pull_requests_with_one_number_apart():
    """Each repository has a #1: the total's numbers carry the repository, so neither pull request hides the other."""
    a = pr(1, "2026-11-01T10:00:00Z", "2026-11-01T11:00:00Z", reviews=[review("approve", 1, "2026-11-01T10:05:00Z")])
    b = pr(1, "2026-11-01T10:00:00Z", "2026-11-01T11:00:00Z", reviews=[review("request-changes", 1, "2026-11-01T10:05:00Z")])
    both = measure.with_repo([a], "o/one") + measure.with_repo([b], "o/two")
    assert [p["number"] for p in both] == ["o/one#1", "o/two#1"]
    rows = dict(measure.loop_numbers(both, "owner"))
    assert rows["round 1 approve"] == "50% (1 of 2)"


def test_a_team_argument_is_a_key_and_a_repository():
    assert measure.team_arg("xyz=acme/tools") == ("XYZ", "acme/tools")
    for wrong in ("XYZ", "XYZ=tools", "=o/n"):
        with pytest.raises(measure.argparse.ArgumentTypeError):
            measure.team_arg(wrong)
    assert measure.team_dump(Path("/t/prs.json"), "XYZ", True) == Path("/t/prs.XYZ.json")
    assert measure.team_dump(Path("/t/prs.json"), "ABC", False) == Path("/t/prs.json"), "one team keeps the file as given"


def test_the_local_review_line_is_read_in_each_form():
    """The local review's line, as the implementer writes it or as the script prints it when it did not run."""
    def with_body(body: str | None) -> dict:
        return {**pr(1, "2026-11-12T10:00:00Z", "2026-11-12T11:00:00Z"), "body": body}

    assert measure.local_review(with_body("Text.\n\nLocal review: 3 findings, 2 fixed, 1 left out\n")) == (3, 2, 1)
    assert measure.local_review(with_body("- Local review: 1 finding, 0 fixed, 1 left out")) == (1, 0, 1), "a bullet, singular"
    assert measure.local_review(with_body("`Local review: 0 findings, 0 fixed, 0 left out`")) == (0, 0, 0)
    assert measure.local_review(with_body("- Local review: did not run (no LOCAL_REVIEW_COMMAND set)")) is None
    assert measure.local_review(with_body("No such line. The local review: 3 findings, 2 fixed, 1 left out")) is None, \
        "only a line that starts with it"
    assert measure.local_review(with_body(None)) is None and measure.local_review(pr(1, "2026-11-12T10:00:00Z", "x")) is None, \
        "an older dump has no text"


def test_the_local_review_rows_split_the_round_1_approve_share():
    def with_body(number: int, body: str, first: str) -> dict:
        reviews = [review(first, 1, "2026-11-12T10:10:00Z")]
        return {**pr(number, "2026-11-12T10:00:00Z", "2026-11-12T11:00:00Z", reviews=reviews), "body": body}

    prs = [with_body(1, "Local review: 3 findings, 2 fixed, 1 left out", "approve"),
           with_body(2, "Local review: 1 finding, 1 fixed, 0 left out", "request-changes"),
           with_body(3, "Local review: did not run (no LOCAL_REVIEW_COMMAND set)", "approve"),
           with_body(4, "No line here.", "request-changes"), with_body(5, "", "request-changes")]
    rows = dict(measure.local_review_numbers(prs))
    assert rows["local review ran"] == "2" and rows["local review did not run, or no line"] == "3"
    assert rows["round 1 approve, local review ran it"] == "50% (1 of 2)"
    assert rows["round 1 approve, local review did not"] == "33% (1 of 3)"
    assert rows["local review findings"] == "4 in all, 3 fixed, 1 left out"
    assert rows["local review findings per pull request"] == "2.00 (#1 3, #2 1)"


def test_a_window_with_no_local_review_line_shows_the_group_ran_it_as_0():
    rows = dict(measure.local_review_numbers([pr(1, "2026-11-12T10:00:00Z", "2026-11-12T11:00:00Z")]))
    assert rows["local review ran"] == "0" and rows["round 1 approve, local review ran it"] == "none"
    assert rows["local review findings"] == "none" and rows["local review findings per pull request"] == "none"


def test_a_clean_needs_decision_is_not_a_pull_request_still_blocked():
    """A needs-decision for the owner's step before the merge with no blocking finding is no block."""
    clean = pr(1, "2026-11-01T10:00:00Z", "2026-11-01T12:00:00Z", reviews=[
        review("request-changes", 1, "2026-11-01T10:05:00Z", [{"blocking": True}]),
        review("needs-decision", 2, "2026-11-01T10:30:00Z", [{"blocking": False}]),
        review("needs-decision", 3, "2026-11-01T11:00:00Z", [])])
    rows = dict(measure.loop_numbers([clean], "owner"))
    assert rows["still blocked after round 2"] == "0" and rows["still blocked after round 3"] == "0"
