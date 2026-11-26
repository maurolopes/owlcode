"""The mechanical checks of the triage (.github/triage.py): each check with a case and a non-case on fake issues, and
`linear.py triage-checks` end to end against the fake Linear of tests/test_linear.py (no network)."""

import importlib.util
import json
import sys
from datetime import UTC, datetime
from types import ModuleType
from typing import Any

import pytest

from tests.conftest import REAL_REPO
from tests.test_linear import Fake, linear, listed


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("github_triage_test", REAL_REPO / ".github" / "triage.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


triage = load()
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
AREAS = set(linear.AREAS)
CLOSED = ["Done", "Canceled", "Duplicate"]


def issue(key: str, state: str, **fields: Any) -> dict[str, Any]:
    """An issue as `linear.py list` prints it."""
    return {"identifier": key, "title": f"Title of {key}", "state": state, "labels": ["workflow"], "priority": "medium",
            "size": "S", "created": "2026-11-01T00:00:00Z", "blocked_by": [], "blocks": [], "related": [], "description": "",
            "comments": [], **fields}


def keys(cases: list[dict[str, str]]) -> list[str]:
    return [c["key"] for c in cases]


def test_the_patterns_are_linear_pys():
    assert triage.ORIGIN.pattern == linear.ORIGIN.pattern and triage.TRIGGER == linear.TRIGGER
    assert triage.KEY.pattern == linear.KEY.pattern


def test_triage_unset_wants_a_priority_and_a_size_only_for_a_pull_request():
    issues = [issue("ABC-1", "Triage", priority=None), issue("ABC-2", "Triage", size=None),
              issue("ABC-3", "Triage", size=None, labels=["workflow", "hands-off"]),
              issue("ABC-4", "Triage", size=None, title="Question: which one?"),
              issue("ABC-5", "Triage"), issue("ABC-6", "Todo", priority=None, size=None)]
    found = triage.triage_unset(issues)
    assert keys(found) == ["ABC-1", "ABC-2"]
    assert found[0]["reason"] == "no priority" and found[1]["reason"] == "no size"


def test_area_count_wants_exactly_one_area():
    issues = [issue("ABC-1", "Todo", labels=[]), issue("ABC-2", "Backlog", labels=["frontend", "backend", "fast-lane"]),
              issue("ABC-3", "Todo", labels=["frontend", "fast-lane"])]
    found = triage.area_count(issues, AREAS)
    assert keys(found) == ["ABC-1", "ABC-2"]
    assert found[0]["reason"] == "no Area label" and found[1]["reason"] == "Area labels frontend, backend"


def test_in_progress_idle_wants_a_pull_request_with_a_recent_push():
    pulls = [{"number": 10, "headRefName": "abc-2-fresh", "commits": [{"committedDate": "2026-11-13T08:00:00Z"}]},
             {"number": 11, "headRefName": "abc-3-stale", "commits": [{"committedDate": "2026-11-10T08:00:00Z"},
                                                                     {"committedDate": "2026-11-11T08:00:00Z"}]},
             {"number": 12, "headRefName": "abc-40-other", "commits": []}]
    stack = [{"body": "Draft stack on ABC-9 (branch abc-9-x)"}]
    ended = [{"body": "Draft stack on ABC-9 (branch abc-9-x)"}, {"body": "Draft stack ended on ABC-9: Escalated"}]
    issues = [issue("ABC-1", "In Progress"), issue("ABC-2", "In Progress"), issue("ABC-3", "In Progress"),
              issue("ABC-4", "In Progress", comments=stack), issue("ABC-5", "In Progress", comments=ended),
              issue("ABC-6", "Todo")]
    found = triage.in_progress_idle(issues, pulls, NOW)
    assert keys(found) == ["ABC-1", "ABC-3", "ABC-5"], "ABC-4 is a draft stack; ABC-40's branch is not ABC-4's"
    assert found[0]["reason"] == "no open pull request" and found[1]["reason"] == "no push for 52 hours (#11)"


def test_backlog_unblocked_lists_an_issue_whose_blockers_are_all_closed():
    issues = [issue("ABC-1", "Backlog", blocked_by=[{"key": "ABC-7", "state": "Done"}, {"key": "ABC-8", "state": "Canceled"}]),
              issue("ABC-2", "Backlog", blocked_by=[{"key": "ABC-7", "state": "Done"}, {"key": "ABC-9", "state": "In Review"}]),
              issue("ABC-3", "Backlog"), issue("ABC-4", "Todo", blocked_by=[{"key": "ABC-7", "state": "Done"}])]
    found = triage.backlog_unblocked(issues, CLOSED)
    assert keys(found) == ["ABC-1"] and "ABC-7 (Done), ABC-8 (Canceled)" in found[0]["reason"]


def test_backlog_unblocked_asks_a_person_to_check_an_issue_with_a_canceled_blocker():
    done, gone = {"key": "ABC-7", "state": "Done"}, {"key": "ABC-8", "state": "Canceled"}
    issues = [issue("ABC-1", "Backlog", blocked_by=[gone, done]), issue("ABC-2", "Backlog", blocked_by=[done]),
              issue("ABC-3", "Backlog", blocked_by=[{"key": "ABC-9", "state": "Duplicate"}])]
    found = triage.backlog_unblocked(issues, CLOSED, ["Canceled"])
    assert found[0]["reason"] == ("a blocker was canceled: ABC-8 (Canceled); check that the issue still holds, or cancel it"
                                  ". Other blockers: ABC-7 (Done)")
    assert found[1]["reason"] == "every blocker is closed: ABC-7 (Done)"
    assert found[2]["reason"] == "every blocker is closed: ABC-9 (Duplicate)"


def test_backlog_untriggered_wants_a_blocker_or_a_trigger_line():
    issues = [issue("ABC-1", "Backlog", description="From: x\n\nWhy"),
              issue("ABC-2", "Backlog", description="Found in ABC-1\nTake it when: 50 runs\n"),
              issue("ABC-3", "Backlog", description="**Take it when:** the owner says so"),
              issue("ABC-4", "Backlog", blocked_by=[{"key": "ABC-9", "state": "Todo"}]), issue("ABC-5", "Todo")]
    assert keys(triage.backlog_untriggered(issues)) == ["ABC-1"]


def test_escalated_stale_counts_from_the_entry_into_the_state():
    issues = [issue("ABC-1", "Escalated", since="2026-11-09T11:00:00Z"), issue("ABC-2", "Escalated", since="2026-11-11T00:00:00Z"),
              issue("ABC-3", "Escalated", created="2026-10-01T00:00:00Z"), issue("ABC-4", "Todo", since="2026-10-01T00:00:00Z")]
    found = triage.escalated_stale(issues, NOW)
    assert keys(found) == ["ABC-1", "ABC-3"], "with no `since`, the issue's creation"
    assert found[0]["reason"] == "in Escalated for 4 days, since 2026-11-09"


def test_fast_lane_broken_checks_the_rules_that_data_can_check():
    good = "Found in ABC-1\n\nWhy\n\n## Needs the owner\n\nNone.\n\n## Done when\n\n* gates"
    issues = [issue("ABC-2", "Todo", labels=["workflow", "fast-lane"], description=good),
              issue("ABC-3", "Todo", labels=["workflow", "fast-lane"], description="From: PR #9, round 1, abc, depth 1\n\n- [ ] R1-1"),
              issue("ABC-4", "Todo", labels=["workflow", "fast-lane"], size="M", description=good),
              issue("ABC-5", "Todo", labels=["workflow", "fast-lane", "hands-off"], description=good),
              issue("ABC-6", "Todo", labels=["workflow", "fast-lane"], description="Why it matters\n\nFound in ABC-1"),
              issue("ABC-7", "Todo", labels=["workflow", "fast-lane"],
                    description="Found in ABC-1\n\n## Needs the owner\n\n* `.github/` is a code owner's path\n\n## Out of scope"),
              issue("ABC-8", "Todo", size="L", description="no origin"), issue("ABC-9", "Triage", labels=["fast-lane"], size="M"),
              # the field as a line (planner.md "The issue"): empty, then not empty
              issue("ABC-10", "Todo", labels=["workflow", "fast-lane"], description="Found in ABC-1\n\n**Needs the owner:** none\n\nWhy"),
              issue("ABC-11", "Todo", labels=["workflow", "fast-lane"],
                    description="Found in ABC-1\n\nNeeds the owner: the owner's review (deploy/ is a code owner's path)"),
              issue("ABC-12", "Todo", labels=["workflow", "fast-lane"], description="Found in ABC-1\n\n- **Needs the owner**:\n  - a live try\n\nWhy")]
    found = {c["key"]: c["reason"] for c in triage.fast_lane_broken(issues)}
    assert list(found) == ["ABC-4", "ABC-5", "ABC-6", "ABC-7", "ABC-11", "ABC-12"]
    assert found["ABC-4"] == "size M, not XS or S" and found["ABC-5"] == "hands-off"
    assert "no origin line" in found["ABC-6"] and found["ABC-7"] == "\"Needs the owner\" is not empty"


def test_report_shows_every_check_and_every_issue_in_triage_in_full():
    issues = [issue("ABC-1", "Triage", priority=None, comments=[{"body": "the owner's answer"}]), issue("ABC-2", "Todo")]
    found = triage.report(issues, [], AREAS, CLOSED, NOW)
    assert list(found["checks"]) == list(triage.CHECKS) and found["read"]["open_issues"] == 2
    assert found["triage"] == [issues[0]] and found["triage"][0]["comments"] == [{"body": "the owner's answer"}]
    assert keys(found["checks"]["triage_unset"]["cases"]) == ["ABC-1"]
    assert found["checks"]["escalated_stale"] == {"rule": triage.CHECKS["escalated_stale"], "cases": []}


def test_the_command_reads_the_team_every_open_issue_and_the_pull_requests(capsys):
    """One call: the team's closed states, every open issue with its relations (`list`), when each issue in Escalated
    entered it, and the pull requests from the `pulls` reader; it prints JSON and moves nothing."""
    class Team(Fake):
        """ABC's states: its Duplicate has the type `duplicate`, which `list` reads as open."""

        def __call__(self, query: str, variables: Any) -> dict[str, Any]:
            if query.startswith("query Team("):
                self.calls.append(("Team", dict(variables)))
                states = [{"id": f"s-{n}", "name": n, "type": t, "position": i} for i, (n, t) in enumerate(
                    [("Backlog", "backlog"), ("Escalated", "started"), ("Done", "completed"), ("Canceled", "canceled"),
                     ("Duplicate", "duplicate")])]
                return {"teams": {"nodes": [{"id": "team-uuid", "key": "ABC", "states": {"nodes": states}}]}}
            return super().__call__(query, variables)

    def blocker(key: str, state: str) -> dict[str, Any]:
        return {"type": "blocks", "issue": {"identifier": key, "state": {"name": state}}}

    area = {"nodes": [{"name": "workflow"}]}
    fake = Team(history=[{"createdAt": "2026-10-01T00:00:00Z", "toState": {"name": "Escalated"}}])
    fake.pages = [[listed("ABC-5", "Triage", estimate=2), listed("ABC-6", "Escalated", priority=3, labels=area),
                   listed("ABC-7", "In Progress", priority=3, estimate=1, labels=area),
                   listed("ABC-8", "Backlog", priority=3, estimate=1, labels=area,
                          inverseRelations={"nodes": [blocker("ABC-1", "Done"), blocker("ABC-2", "Duplicate")]}),
                   listed("ABC-9", "Duplicate"),
                   listed("ABC-10", "Backlog", priority=3, estimate=1, labels=area,
                          inverseRelations={"nodes": [blocker("ABC-3", "Canceled")]})]]
    pulls = [{"number": 3, "headRefName": "abc-7-x", "commits": [{"committedDate": datetime.now(UTC).isoformat()}]}]
    assert linear.run(["triage-checks", "abc"], post=fake, pulls=lambda: pulls) == 0
    found = json.loads(capsys.readouterr().out)
    checks = {name: keys(check["cases"]) for name, check in found["checks"].items()}
    assert checks["triage_unset"] == ["ABC-5"] and checks["area_count"] == ["ABC-5"]
    assert checks["escalated_stale"] == ["ABC-6"] and checks["in_progress_idle"] == []
    assert checks["backlog_unblocked"] == ["ABC-8", "ABC-10"], "a blocker in Duplicate is closed"
    reasons = {c["key"]: c["reason"] for c in found["checks"]["backlog_unblocked"]["cases"]}
    assert reasons["ABC-10"].startswith("a blocker was canceled: ABC-3 (Canceled)")
    assert reasons["ABC-8"].startswith("every blocker is closed")
    assert "ABC-9" not in json.dumps(found["checks"]) and found["read"]["open_issues"] == 5, "an issue in Duplicate is not open"
    assert [i["identifier"] for i in found["triage"]] == ["ABC-5"] and found["read"]["open_pull_requests"] == 1
    assert [n for n, _ in fake.calls if n == "Entered"] == ["Entered"], "only the issue in Escalated"
    assert not {"Move", "Set", "Comment", "Create", "Describe", "Label", "Unlabel"} & {n for n, _ in fake.calls}


def test_open_pulls_reads_each_pull_requests_last_commit_in_gh_pr_lists_shape(monkeypatch):
    """`gh pr list --limit 500 --json commits` asks GitHub for too many nodes (refused, 2026-11-13): one paged GraphQL query
    reads only the last commit of each, and a failure of gh stops the command with the reason."""
    import subprocess

    lines = ['{"number": 7, "headRefName": "abc-7-x", "isDraft": true, "commits": {"nodes": [{"commit": {"committedDate": "2026-11-12T10:00:00Z"}}]}}',
             '{"number": 8, "headRefName": "abc-8-y", "isDraft": false, "commits": {"nodes": []}}', ""]
    seen: list[list[str]] = []

    def fake_run(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        seen.append(command)
        return subprocess.CompletedProcess(command, 0, "\n".join(lines), "")

    monkeypatch.setattr(linear.subprocess, "run", fake_run)
    assert linear.open_pulls() == [{"number": 7, "headRefName": "abc-7-x", "isDraft": True, "commits": [{"committedDate": "2026-11-12T10:00:00Z"}]},
                                   {"number": 8, "headRefName": "abc-8-y", "isDraft": False, "commits": []}]
    assert seen[0][:4] == ["gh", "api", "graphql", "--paginate"] and "states: OPEN" in " ".join(seen[0])
    monkeypatch.setattr(linear.subprocess, "run", lambda command, **_: subprocess.CompletedProcess(command, 1, "", "HTTP 401"))
    with pytest.raises(linear.Failed, match="HTTP 401"):
        linear.open_pulls()
