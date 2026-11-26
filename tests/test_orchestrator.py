"""The orchestrator's reading half: the pure decisions with the states of agents/README.md "The issue states", and the
loop with fakes for the two reads. No network, no `gh`."""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import os
import re
import shlex
import sys
import tempfile
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from collections.abc import Callable, Sequence
from typing import Any

import pytest

REAL_REPO = Path(__file__).resolve().parent.parent


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("orchestrator_main", REAL_REPO / "orchestrator" / "orchestrator.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


orch = _load()
def _neutral_example() -> str:
    """The example config's text with the neutral team ABC: the tests read the team's key from it."""
    text = (REAL_REPO / "orchestrator" / "orchestrator.example.toml").read_text()
    text = re.sub(r'^team = ".*"$', 'team = "ABC"', text, count=1, flags=re.MULTILINE)
    for old, new in (("ABC_LINEAR", "DEF_LINEAR"), ("abc-linear", "def-linear"), ("acme/abc", "acme/def"), ("~/abc", "~/def")):
        text = text.replace(old, new)
    return text.replace('# team = "ABC"', '# team = "DEF"')


EXAMPLE_TEXT = _neutral_example()


@pytest.fixture(autouse=True)
def fourteen_cpus(monkeypatch: pytest.MonkeyPatch) -> None:
    """The run lines and commands hold the pytest workers (`pytest_workers`): the tests read them on a Mac of 14 CPUs, whatever the machine."""
    monkeypatch.setattr(orch.os, "cpu_count", lambda: 14)


def example_file(folder: Path) -> Path:
    """The neutral example as a file of the test's own folder."""
    path = folder / "orchestrator.example.toml"
    path.write_text(EXAMPLE_TEXT)
    return path
with tempfile.TemporaryDirectory() as _folder:
    EXAMPLE = orch.load_config(example_file(Path(_folder)))  # the example's own values (three slots, no Area rule, only `hands-off` skipped)
# The settings that the example changed later are still settings. Most tests read the rules with the values of before:
# two slots, one run per Area, and the skip label `workflow`, which the plan counts as `hands-off`.
BEFORE_929 = {"max_runs": "max_runs = 2", "distinct_areas": "distinct_areas = true",
              "skip_labels": 'skip_labels = ["hands-off", "workflow"]'}


def _with_lines(text: str, lines: dict[str, str | None]) -> str:
    for key, line in lines.items():
        text, n = re.subn(rf"^{key}\s*=.*$\n?", "" if line is None else line + "\n", text, count=1, flags=re.MULTILINE)
        assert n == 1, key
    return text


TEST_TOML = _with_lines(EXAMPLE_TEXT, dict(BEFORE_929))  # the example with the values of before that change
CFG = dataclasses.replace(EXAMPLE, max_runs=2, distinct_areas=True, skip_labels=("hands-off", "workflow"))  # TEST_TOML's values


def config_with(tmp_path: Path, **lines: str | None) -> Path:
    """The tests' file (TEST_TOML) with each named key's line replaced (`key = value`) or, with None, removed: the config has no defaults."""
    text = _with_lines(TEST_TOML, lines)
    path = tmp_path / "c.toml"
    path.write_text(text)
    return path


def issue(key: str = "ABC-1", state: str = "Todo", labels: tuple[str, ...] = (), blocked: tuple[tuple[str, str], ...] = (),
          size: str | None = "S", priority: str | None = "medium", area: str | None = None, related: tuple[str, ...] = (),
          description: str = "", manual_order: float | None = None) -> dict[str, Any]:
    return {"identifier": key, "state": state, "labels": list(labels) + ([area] if area else []), "size": size,
            "priority": priority, "manual_order": manual_order, "blocked_by": [{"key": k, "state": s} for k, s in blocked],
            "related": [{"key": k, "state": "Todo"} for k in related], "description": description}


UPDATER = "acme-implementer"
CONFLICT = "The branch update found a merge conflict with main. Auto-merge is off until the branch merges main."  # verdict.py's words


def pr(number: int = 1, branch: str = "abc-1-thing", draft: bool = False, reviews: tuple[tuple[str, str], ...] = (),
       checks: tuple[tuple[str, str], ...] = (), comments: tuple[tuple[str, ...], ...] = (), commit: str = "2026-11-01T10:00:00Z") -> dict[str, Any]:
    return {"number": number, "headRefName": branch, "isDraft": draft,
            "reviews": [{"state": s, "submittedAt": at} for s, at in reviews],
            "statusCheckRollup": [{"name": n, "status": "COMPLETED", "conclusion": c} for n, c in checks],
            "comments": [{"body": c[0], "createdAt": c[1], "author": {"login": c[2] if len(c) > 2 else UPDATER}} for c in comments], "commits": [{"committedDate": commit}]}


GREEN = (("python", "SUCCESS"), ("web", "SUCCESS"), ("verdict", "SUCCESS"))


@pytest.fixture(autouse=True)
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The config's default state file is under the home folder: a test never writes the real one."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


STAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")


def unstamped(text: str) -> list[str]:
    return [STAMP.sub("T", line) for line in text.splitlines()]


class FakeHost:
    """A session host that records its starts; a test ends a session by putting it in `ended`."""

    def __init__(self) -> None:
        self.starts: list[tuple[str, str, str]] = []
        self.ended: set[str] = set()
        self.spaces: list[dict[str, Any]] = []  # what `workspaces` lists
        self.closed: list[str] = []
        self.refuse: set[str] = set()  # the workspaces whose close fails
        self.wakes: list[tuple[str, str]] = []  # (key, text) of each `wake` call
        self.wakeable = False  # what `wake` answers: True once a test has an idle session to reach
        self.wake_error: Exception | None = None  # raised by `wake` when set
        self.pause: tuple[str, str] | None = None  # the stop of a session with no profile label: `paused` answers it for every profile
        self.pauses: dict[str, tuple[str, str]] = {}  # profile -> its newest session's stop
        self.failed_sessions: set[str] = set()  # the sessions that stopped in error before their claim

    def start(self, label: str, command: str, cwd: str) -> str:
        self.starts.append((label, command, cwd))
        return f"s{len(self.starts)}"

    def alive(self, session: str) -> bool:
        return session not in self.ended

    def failed(self, session: str) -> bool:
        return session in self.failed_sessions

    def tail(self, session: str) -> str:
        return f"last lines of {session}"

    def workspaces(self) -> list[dict[str, Any]]:
        return self.spaces

    def close(self, workspace: str) -> None:
        if workspace in self.refuse:
            raise RuntimeError("herdr workspace close failed: no such workspace")
        self.closed.append(workspace)

    def wake(self, key: str, text: str) -> bool:
        self.wakes.append((key, text))
        if self.wake_error is not None:
            raise self.wake_error
        return self.wakeable

    def paused(self) -> dict[str | None, tuple[str, str]]:
        return {**({None: self.pause} if self.pause else {}), **self.pauses}



# -- can_start: Todo, no hands-off, no open blocker, not size L ----------------------------------------------------------


def test_can_start_a_plain_todo_issue() -> None:
    assert orch.can_start(issue(), CFG)


@pytest.mark.parametrize("state", ["Triage", "Backlog", "In Progress", "In Review", "Approved", "Escalated", "Done", "Canceled"])
def test_can_start_only_from_todo(state: str) -> None:
    assert not orch.can_start(issue(state=state), CFG)


def test_hands_off_and_workflow_never_start() -> None:
    assert not orch.can_start(issue(labels=("hands-off",)), CFG)
    assert not orch.can_start(issue(labels=("workflow",)), CFG)
    assert orch.can_start(issue(labels=("backend", "fast-lane")), CFG)


def test_an_open_blocker_stops_a_start_and_a_closed_one_does_not() -> None:
    assert not orch.can_start(issue(blocked=(("ABC-9", "In Review"),)), CFG)
    assert not orch.can_start(issue(blocked=(("ABC-8", "Done"), ("ABC-9", "Backlog"))), CFG)
    assert orch.can_start(issue(blocked=(("ABC-8", "Done"), ("ABC-7", "Canceled"))), CFG)


def test_size_l_is_split_first_and_no_size_is_not_l() -> None:
    assert not orch.can_start(issue(size="L"), CFG)
    assert orch.can_start(issue(size=None), CFG)


def test_draft_stack_needs_one_open_blocker_in_review_with_its_pull_request() -> None:
    stacked = issue(blocked=(("ABC-9", "In Review"),))
    assert orch.stack_blocker(stacked, CFG, {"ABC-9"}) == "ABC-9"
    assert orch.stack_blocker(stacked, CFG, set()) is None  # no pull request open
    assert orch.stack_blocker(issue(blocked=(("ABC-9", "In Progress"),)), CFG, {"ABC-9"}) is None
    assert orch.stack_blocker(issue(blocked=(("ABC-9", "Approved"), ("ABC-8", "In Review"))), CFG, {"ABC-9", "ABC-8"}) is None
    assert orch.stack_blocker(issue(blocked=(("ABC-9", "Approved"),), labels=("hands-off",)), CFG, {"ABC-9"}) is None
    assert orch.stack_blocker(issue(blocked=(("ABC-9", "Approved"),), size="L"), CFG, {"ABC-9"}) is None


# -- pr_state: draft, waiting for the verdict, approved, changes requested, failed ---------------------------------------


def test_pr_state_draft_wins() -> None:
    assert orch.pr_state(pr(draft=True, reviews=(("CHANGES_REQUESTED", "2026-11-02T10:00:00Z"),)), CFG) == "draft"


def test_pr_state_waiting_with_no_verdict_yet() -> None:
    assert orch.pr_state(pr(), CFG) == "waiting"
    assert orch.pr_state(pr(checks=(("python", "SUCCESS"),)), CFG) == "waiting"
    assert orch.pr_state(pr(reviews=(("COMMENTED", "2026-11-02T10:00:00Z"),)), CFG) == "waiting"


def test_pr_state_approved_needs_the_approve_and_a_passing_verdict_check() -> None:
    assert orch.pr_state(pr(reviews=(("APPROVED", "2026-11-02T10:00:00Z"),), checks=GREEN), CFG) == "approved"
    assert orch.pr_state(pr(reviews=(("APPROVED", "2026-11-02T10:00:00Z"),), checks=(("python", "SUCCESS"),)), CFG) == "waiting"


def test_pr_state_changes_requested_by_the_latest_review() -> None:
    reviews = (("APPROVED", "2026-11-02T10:00:00Z"), ("CHANGES_REQUESTED", "2026-11-03T10:00:00Z"))
    assert orch.pr_state(pr(reviews=reviews), CFG) == "changes requested"
    again = (("CHANGES_REQUESTED", "2026-11-02T10:00:00Z"), ("APPROVED", "2026-11-04T10:00:00Z"))
    assert orch.pr_state(pr(reviews=again, checks=GREEN), CFG) == "approved"


def test_pr_state_failed_required_check_even_after_an_approve() -> None:
    reviews = (("APPROVED", "2026-11-02T10:00:00Z"),)
    assert orch.pr_state(pr(reviews=reviews, checks=(("python", "FAILURE"), ("verdict", "SUCCESS"))), CFG) == "failed"
    assert orch.pr_state(pr(checks=(("web", "CANCELLED"),)), CFG) == "failed"


def test_pr_state_failed_verdict_check_is_failed_unless_changes_were_requested() -> None:
    assert orch.pr_state(pr(reviews=(("APPROVED", "2026-11-02T10:00:00Z"),), checks=(("verdict", "FAILURE"),)), CFG) == "failed"
    assert orch.pr_state(pr(reviews=(("CHANGES_REQUESTED", "2026-11-02T10:00:00Z"),), checks=(("verdict", "FAILURE"),)), CFG) == "changes requested"


def test_pr_state_a_running_or_skipped_check_is_not_a_failure() -> None:
    running = {"name": "python", "status": "IN_PROGRESS", "conclusion": ""}
    assert orch.pr_state(dict(pr(), statusCheckRollup=[running]), CFG) == "waiting"
    assert orch.pr_state(pr(checks=(("web", "SKIPPED"),)), CFG) == "waiting"


def test_pr_state_reads_a_status_context_by_its_context_and_state() -> None:
    context = {"context": "python", "state": "FAILURE"}
    assert orch.pr_state(dict(pr(), statusCheckRollup=[context]), CFG) == "failed"


def run(name: str, conclusion: str, started: str, status: str = "COMPLETED") -> dict[str, Any]:
    return {"name": name, "status": status, "conclusion": conclusion, "startedAt": started}


def test_pr_state_judges_the_newest_run_of_each_check_only() -> None:
    approve = (("APPROVED", "2026-11-02T09:00:00Z"),)
    rolled = [run("python", "CANCELLED", "2026-11-02T10:00:00Z"), run("python", "SUCCESS", "2026-11-02T10:05:00Z"),
              run("web", "SUCCESS", "2026-11-02T10:05:00Z"),
              run("verdict", "FAILURE", "2026-11-02T10:10:00Z"), run("verdict", "SUCCESS", "2026-11-02T10:20:00Z")]
    assert orch.pr_state(dict(pr(reviews=approve), statusCheckRollup=rolled), CFG) == "approved"
    assert orch.pr_state(dict(pr(reviews=approve), statusCheckRollup=list(reversed(rolled))), CFG) == "approved"
    # the other way round: the newest run is the bad one
    worse = [run("python", "SUCCESS", "2026-11-02T10:00:00Z"), run("python", "FAILURE", "2026-11-02T10:05:00Z")]
    assert orch.pr_state(dict(pr(), statusCheckRollup=worse), CFG) == "failed"


def test_newest_checks_a_running_run_is_newest_and_a_skip_replaces_only_a_cancel() -> None:
    running = run("python", "", "0001-01-01T00:00:00Z", status="IN_PROGRESS")
    kept = orch.newest_checks([run("python", "FAILURE", "2026-11-02T10:00:00Z"), running])
    assert kept == [running]
    skipped = run("web", "SKIPPED", "2026-11-02T11:00:00Z")
    assert orch.newest_checks([run("web", "FAILURE", "2026-11-02T10:00:00Z"), skipped])[0]["conclusion"] == "FAILURE"
    assert orch.newest_checks([run("web", "CANCELLED", "2026-11-02T10:00:00Z"), skipped])[0]["conclusion"] == "SKIPPED"


# -- can_resume ------------------------------------------------------------------------------------------------------


def test_can_resume_after_changes_requested_or_failed_gates() -> None:
    asked = pr(reviews=(("CHANGES_REQUESTED", "2026-11-02T10:00:00Z"),))
    assert orch.can_resume(issue(state="In Progress"), asked, CFG) == "changes requested"
    assert orch.can_resume(issue(state="In Progress"), pr(checks=(("python", "FAILURE"),)), CFG) == "failed"


def test_can_resume_not_while_the_pull_request_is_a_draft_or_waits_or_is_approved() -> None:
    inprog = issue(state="In Progress")
    assert orch.can_resume(inprog, pr(draft=True, reviews=(("CHANGES_REQUESTED", "2026-11-02T10:00:00Z"),)), CFG) is None
    assert orch.can_resume(inprog, pr(), CFG) is None
    assert orch.can_resume(inprog, pr(reviews=(("APPROVED", "2026-11-02T10:00:00Z"),), checks=GREEN), CFG) is None
    assert orch.can_resume(inprog, None, CFG) is None


def test_can_resume_only_in_progress() -> None:
    asked = pr(reviews=(("CHANGES_REQUESTED", "2026-11-02T10:00:00Z"),))
    for state in ("Todo", "In Review", "Approved", "Escalated"):
        assert orch.can_resume(issue(state=state), asked, CFG) is None
        assert orch.can_resume(issue(state=state, labels=("restart",)), asked, CFG) is None


def test_can_resume_never_for_hands_off_or_workflow_issues() -> None:
    asked = pr(reviews=(("CHANGES_REQUESTED", "2026-11-02T10:00:00Z"),))
    for label in ("hands-off", "workflow"):
        marked = issue(state="In Progress", labels=(label,))
        assert orch.can_resume(marked, asked, CFG) is None
        assert orch.can_resume(issue(state="In Progress", labels=(label, "restart")), None, CFG) is None
        assert orch.can_resume(issue(state="In Progress", labels=(label, "restart")), asked, CFG) is None
    runner, held = orch.Runner(CFG, FakeHost()), [issue(state="In Progress", labels=("workflow", "restart"))]
    assert runner.fix_round(held, [], dry=True) == [] and runner.used(held, []) == {"ABC-1"}


def test_can_resume_on_the_restart_label_with_or_without_a_pull_request() -> None:
    marked = issue(state="In Progress", labels=("restart",))
    assert orch.can_resume(marked, None, CFG) == "restart label"
    assert orch.can_resume(marked, pr(draft=True), CFG) == "restart label"


def test_can_resume_on_a_conflict_comment_newer_than_the_last_commit_only() -> None:
    inprog = issue(state="In Progress")
    said = ((CONFLICT, "2026-11-02T12:00:00Z"),)
    assert orch.can_resume(inprog, pr(comments=said), CFG) == "conflict with main"
    fixed = pr(comments=said, commit="2026-11-02T13:00:00Z")  # the branch moved after the comment
    assert orch.can_resume(inprog, fixed, CFG) is None
    assert orch.can_resume(inprog, pr(comments=(("looks fine", "2026-11-02T12:00:00Z"),)), CFG) is None


def test_the_example_config_names_the_branch_updates_real_words() -> None:
    words = re.search(r'^CONFLICT = "(.+)"$', (REAL_REPO / ".github" / "verdict.py").read_text(), re.MULTILINE)
    assert words is not None and CFG.conflict_comment == words.group(1)
    assert CONFLICT.startswith(CFG.conflict_comment) and CFG.conflict_author == UPDATER


def test_only_the_branch_updates_own_comment_counts_as_a_conflict() -> None:
    inprog, at = issue(state="In Progress"), "2026-11-02T12:00:00Z"
    by_a_person = ((CONFLICT, at, "someone"),)  # the right words, another author
    named = (("Is this a merge conflict with main? The branch update found a merge conflict with main.", at),)  # not its first words
    old_words = (("This branch has a conflict with main: merge it.", at),)
    for comments in (by_a_person, named, old_words):
        assert orch.can_resume(inprog, pr(comments=comments), CFG) is None
        assert orch.waiting_since(pr(comments=comments), "conflict with main", CFG) is None


# -- pr_for and plan -------------------------------------------------------------------------------------------------


def test_pr_for_matches_the_key_in_the_branch_and_not_a_longer_number() -> None:
    prs = [pr(1, "abc-12-a"), pr(2, "abc-123-b"), pr(3, "abc-12-a-2")]
    found = orch.pr_for("ABC-12", prs)
    assert found is not None and found["number"] == 3
    assert orch.pr_for("ABC-1", prs) is None
    assert orch.pr_for("ABC-99", prs) is None


def test_stack_pairs_names_the_draft_stacks_only() -> None:
    issues = [issue("ABC-2"), issue("ABC-8", blocked=(("ABC-9", "In Review"),)), issue("ABC-9", state="In Review")]
    assert orch.stack_pairs(issues, [pr(9, "abc-9-y")], CFG) == [("ABC-8", "ABC-9")]


def no_area(_issue: Any) -> None:
    return None


def by_label(one: Any) -> Any:
    return orch.own_area(one.get("labels") or [], CFG)


def picked(out: Any) -> list[str]:
    return [key for key, waits in out if waits is None]


def test_pick_starts_takes_priority_then_number_and_stops_at_the_free_slots() -> None:
    issues = [issue("ABC-3", size="XS", priority="high", area="backend"), issue("ABC-2", size="M", priority="high", area="docs"),
              issue("ABC-4", size="XS", priority="low", area="infra"), issue("ABC-6", priority=None, area="frontend")]
    assert picked(orch.pick_starts(issues, [], 4, True, by_label)) == ["ABC-2", "ABC-3", "ABC-4", "ABC-6"]
    out = orch.pick_starts(issues, [], 2, True, by_label)
    assert out == [("ABC-2", None), ("ABC-3", None), ("ABC-4", "no free slot"), ("ABC-6", "no free slot")]
    assert picked(orch.pick_starts(issues, [], 0, True, by_label)) == []


def test_issues_of_one_priority_start_in_the_owners_order_whatever_their_sizes() -> None:
    """The owner drags the issues in Linear: the lower `manual_order` starts first, the size does not count."""
    issues = [issue("ABC-1", size="XS", manual_order=-10.5, area="docs"), issue("ABC-2", size="M", manual_order=-200, area="backend"),
              issue("ABC-3", size="S", manual_order=-50, area="infra")]
    assert picked(orch.pick_starts(issues, [], 3, True, by_label)) == ["ABC-2", "ABC-3", "ABC-1"]


def test_priority_comes_before_the_owners_order() -> None:
    issues = [issue("ABC-1", priority="low", manual_order=-1000, area="docs"), issue("ABC-2", priority="high", manual_order=5, area="backend"),
              issue("ABC-3", priority=None, manual_order=-2000, area="infra")]
    assert picked(orch.pick_starts(issues, [], 3, True, by_label)) == ["ABC-2", "ABC-1", "ABC-3"]


def test_an_issue_without_the_owners_order_comes_after_the_ones_with_it_then_by_number() -> None:
    """A read from before the field (no `manual_order`, or a value that is no number) sorts after the placed issues."""
    old = {k: v for k, v in issue("ABC-4", area="frontend").items() if k != "manual_order"}
    issues = [issue("ABC-9", area="docs"), old, issue("ABC-12", manual_order=3, area="backend"),
              {**issue("ABC-5", area="infra"), "manual_order": True}]
    assert picked(orch.pick_starts(issues, [], 4, True, by_label)) == ["ABC-12", "ABC-4", "ABC-5", "ABC-9"]


def test_pick_starts_one_run_per_area_when_distinct_areas_is_on() -> None:
    docs = [issue("ABC-635", size="XS", area="docs"), issue("ABC-602", priority="low", size="XS", area="docs"),
            issue("ABC-645", area="frontend")]
    assert orch.pick_starts(docs, [], 2, True, by_label) == [
        ("ABC-635", None), ("ABC-645", None), ("ABC-602", "area docs: ABC-635 runs")]
    running = [issue("ABC-9", state="In Review", area="frontend")]
    assert orch.pick_starts(docs, running, 2, True, by_label)[1] == ("ABC-645", "area frontend: ABC-9 runs")
    # off: the same Area runs in parallel
    assert picked(orch.pick_starts(docs, [], 3, False, by_label)) == ["ABC-635", "ABC-645", "ABC-602"]


def test_pick_starts_never_runs_two_related_issues_whatever_the_area_rule() -> None:
    a, b = issue("ABC-20", area="docs", related=("ABC-21",)), issue("ABC-21", area="backend")
    assert orch.pick_starts([a, b], [], 2, False, by_label) == [("ABC-20", None), ("ABC-21", "related to ABC-20, which runs")]
    running = [issue("ABC-30", state="In Progress", area="infra", related=("ABC-21",))]  # the link read from the other side
    assert orch.pick_starts([b], running, 2, False, by_label) == [("ABC-21", "related to ABC-30, which runs")]


def test_pick_starts_holds_an_issue_whose_area_is_unknown_while_the_area_rule_is_on() -> None:
    bare = issue("ABC-40")
    assert orch.pick_starts([bare], [], 2, True, no_area) == [("ABC-40", "no Area label: add one")]
    assert orch.pick_starts([bare], [], 2, False, no_area) == [("ABC-40", None)]


def test_a_follow_up_with_no_area_takes_the_area_of_its_origin() -> None:
    follow = issue("ABC-50", description="Found in ABC-49\n\nmore text")
    assert orch.origin_key(follow) == "ABC-49"
    assert orch.origin_key(issue(description="From: [ABC-46](https://linear.app/x/issue/ABC-46/y) (split)")) == "ABC-46"
    assert orch.origin_key(issue(description="From: PR #60, round 2, 90226b59, depth 1")) == "#60"
    assert orch.origin_key(issue(description="Some text")) is None
    assert orch.area_of(follow, CFG, {"ABC-49": "infra"}) == "infra"
    assert orch.area_of(issue(area="docs", description="Found in ABC-49"), CFG, {"ABC-49": "infra"}) == "docs"
    assert orch.own_area(["docs", "backend"], CFG) is None  # two Areas: none is the one


# -- config ----------------------------------------------------------------------------------------------------------


def test_the_example_config_loads_and_dry_run_is_the_default() -> None:
    assert EXAMPLE.dry_run is True and EXAMPLE.poll_seconds == 60 and EXAMPLE.max_runs == 3  # three: the owner's call
    assert CFG.team == "ABC" and CFG.required_checks == ("python", "web")


def test_config_errors_name_the_problem(tmp_path: Path) -> None:
    for line, words in (("poll_seconds = 0", "poll_seconds"), ("dry_run = 'yes'", "dry_run"), ("team = ''", "team"),
                        ("session_host = 'ssh'", "session_host")):
        key = line.split()[0]
        with pytest.raises(orch.ConfigError, match=words):
            orch.load_config(config_with(tmp_path, **{key: line}))
    path = tmp_path / "c.toml"
    path.write_text("this is not toml")
    with pytest.raises(orch.ConfigError, match="cannot read"):
        orch.load_config(path)


def test_the_example_loads_with_no_error_and_its_keys_are_the_ones_the_code_reads() -> None:
    data = tomllib.loads(EXAMPLE_TEXT)
    profiles = data.pop("profiles")  # tables of names that the owner chooses, read apart (PROFILE_KEYS)
    harnesses = data.pop("harnesses")  # the same, for the harnesses (HARNESS_KEYS)
    keys = {("", k) for k, v in data.items() if not isinstance(v, dict)} | {(t, k) for t, v in data.items() if isinstance(v, dict) for k in v}
    assert keys == {(t, k) for t, k, _, _ in orch.SCHEMA}  # as limits.toml and config.LIMIT_DEFAULTS
    lines = set(orch.example_lines(EXAMPLE_TEXT))
    assert {(t, k) for t, k in lines if not t.startswith(("profiles.", "harnesses."))} == keys
    # each profile of the example names every key, and the optional ones only where they apply: `permission_mode` where its
    # harness takes one (pi takes none), `credential` where it is not the keychain
    takes = {n: (*orch.PROFILE_KEYS, *(k for k in orch.PROFILE_OPTIONAL if k in t)) for n, t in profiles.items()}
    assert {(t, k) for t, k in lines if t.startswith("profiles.")} == {(f"profiles.{n}", k) for n in profiles for k in takes[n]}
    assert all(set(table) == set(takes[n]) for n, table in profiles.items())
    assert {k for table in profiles.values() for k in table if k in orch.PROFILE_OPTIONAL} == set(orch.PROFILE_OPTIONAL), \
        "the example shows each optional key"
    assert {n for n, t in profiles.items() if "permission_mode" not in t} == {"pi-ollama", "pi-chatgpt", "pi-lithosai"}
    assert {(t, k) for t, k in lines if t.startswith("harnesses.")} == {(f"harnesses.{n}", k) for n in harnesses for k in orch.HARNESS_KEYS}


def test_a_missing_key_stops_the_start_with_its_line_and_every_missing_key_shows_at_once(tmp_path: Path) -> None:
    with pytest.raises(orch.ConfigError) as one:
        orch.load_config(config_with(tmp_path, both=None))
    assert 'missing in' in str(one.value) and '[profiles.claude] both = ["claude-opus-5-5", "high"]' in str(one.value)
    with pytest.raises(orch.ConfigError) as several:
        orch.load_config(config_with(tmp_path, poll_seconds=None, queue_issue=None, repo=None, inference_profile=None))
    text = str(several.value)
    for line in ("poll_seconds = 60", 'queue_issue = ""', '[github] repo = "acme/product"', 'inference_profile = "claude"'):
        assert line in text
    assert "#" not in text  # the comment stays in the example
    # a file with nothing in it: all of the keys, none a default
    path = tmp_path / "empty.toml"
    path.write_text("")
    with pytest.raises(orch.ConfigError) as none:
        orch.load_config(path)
    assert str(none.value).count("\n") == len(orch.SCHEMA)


def test_with_no_example_beside_the_code_a_missing_key_is_named_with_a_place_holder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orch, "_example_text", lambda: "")  # the installed copy holds no example
    with pytest.raises(orch.ConfigError) as e:
        orch.load_config(config_with(tmp_path, both=None))
    assert "[profiles.claude] both = ?  (see orchestrator.example.toml)" in str(e.value)


def test_an_unknown_key_stops_the_start_by_name(tmp_path: Path) -> None:
    path = config_with(tmp_path, poll_seconds="poll_second = 60")
    with pytest.raises(orch.ConfigError) as e:
        orch.load_config(path)
    assert "unknown" in str(e.value) and "poll_second" in str(e.value) and "missing" in str(e.value) and "poll_seconds = 60" in str(e.value)
    path.write_text(EXAMPLE_TEXT.replace("[github]", "[github]\nrepos = 1"))
    with pytest.raises(orch.ConfigError, match=r"unknown.*\[github\] repos"):
        orch.load_config(path)


# -- the loop --------------------------------------------------------------------------------------------------------


def reads_of(issues: Any, prs: Any) -> Any:
    def get(value: Any) -> Any:
        def read() -> Any:
            if isinstance(value, Exception):
                raise value
            return value
        return read
    return orch.Reads(issues=get(issues), prs=get(prs))


def test_a_round_prints_the_plan_and_writes_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    assert orch.round_once(CFG, reads_of([issue("ABC-2", area="backend")], [])) is True
    assert unstamped(capsys.readouterr().out) == ["T build 0/2 · review 0 · you 0", "would start ABC-2 (claude-sonnet-5-5, medium)",
                                                  "backend  ABC-2 (starting)", '{"event": "would_start", "key": "ABC-2", "model": "claude-sonnet-5-5", "effort": "medium", "time": "T"}']


def test_a_failed_read_logs_and_the_next_round_goes_on() -> None:
    said: list[str] = []
    assert orch.round_once(CFG, reads_of(RuntimeError("Linear did not answer"), []), said.append) is False
    assert unstamped("\n".join(said)) == ["T round failed: RuntimeError: Linear did not answer"]
    assert orch.round_once(CFG, reads_of([], RuntimeError("gh failed")), said.append) is False


def test_main_sleeps_poll_seconds_between_rounds_and_a_failed_read_never_ends_it(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = tmp_path / "c.toml"
    config.write_text(TEST_TOML.replace("poll_seconds = 60", "poll_seconds = 7"))
    answers = [RuntimeError("down"), [issue("ABC-2", area="docs")], [issue("ABC-3", area="docs")]]
    slept: list[float] = []

    def issues() -> Any:
        value = answers.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        if len(slept) == 3:
            raise KeyboardInterrupt

    assert orch.main(["--config", str(config)], orch.Reads(issues=issues, prs=lambda: []), sleep, host=FakeHost()) == 0
    assert slept == [7, 7, 7]
    printed = unstamped(capsys.readouterr().out)
    assert [line for line in printed if not line.startswith("{")] == [  # the service's log: each line starts with its time
        "T round failed: RuntimeError: down", "T build 0/2 · review 0 · you 0", "would start ABC-2 (claude-sonnet-5-5, medium)", "docs  ABC-2 (starting)",
        "T build 0/2 · review 0 · you 0", "would start ABC-3 (claude-sonnet-5-5, medium)", "docs  ABC-3 (starting)"]
    assert (tmp_path / "home" / ".local" / "state" / "orchestrator" / "state.json").exists()  # the config's default path


def test_main_with_log_appends_each_line_to_the_file_and_a_rotation_loses_none(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """newsyslog renames the log and makes a new one. The loop opens the file for each line, so the round after the
    rotation writes to the new file, and `--check` reads the newest lines there."""
    config = tmp_path / "c.toml"
    config.write_text(EXAMPLE_TEXT)
    log = tmp_path / "orchestrator.log"
    answers = [[issue("ABC-2", area="docs")], [issue("ABC-3", area="docs")]]
    slept: list[float] = []

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        if len(slept) == 1:
            log.rename(tmp_path / "orchestrator.log.0")  # newsyslog's rotation, the flag N: no signal
            log.touch()
        else:
            raise KeyboardInterrupt

    assert orch.main(["--config", str(config), "--log", str(log)], orch.Reads(issues=lambda: answers.pop(0), prs=lambda: []), sleep, host=FakeHost()) == 0
    assert capsys.readouterr().out == ""  # every line went to the file
    old = (tmp_path / "orchestrator.log.0").read_text()
    assert "would start ABC-2 (claude-sonnet-5-5, medium)" in old and "ABC-3" not in old
    tail = orch.read_log_tail(log)
    assert tail is not None and "would start ABC-3 (claude-sonnet-5-5, medium)" in tail and "ABC-2" not in tail


def test_main_with_log_prints_a_line_that_the_file_cannot_take(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A log that cannot be written never ends the process (a crash loop): the line goes to standard output, the crash log."""
    log = tmp_path / "missing" / "orchestrator.log"
    assert orch.main(["--config", str(example_file(tmp_path)), "--once", "--log", str(log)], reads_of([], []), host=FakeHost()) == 0
    out = unstamped(capsys.readouterr().out)
    assert out == ["log not written (FileNotFoundError): T build 0/3 · review 0 · you 0", "log not written (FileNotFoundError): nothing to do"]


def test_check_reads_a_waiting_line_that_the_service_wrote_after_a_rotation(tmp_path: Path) -> None:
    """service.py's `waiting:` lines go to the log the same way; after a rotation `--check` reads the new file."""
    spec = importlib.util.spec_from_file_location("orchestrator_service_log", REAL_REPO / "orchestrator" / "deploy" / "service.py")
    assert spec is not None and spec.loader is not None
    service = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = service
    spec.loader.exec_module(service)
    log = tmp_path / "orchestrator.log"
    write = service.append_to(str(log))
    write("2026-11-10T11:00:00Z waiting: the agent's keychain is locked (the check exited 51); next try in 60 s")
    log.rename(tmp_path / "orchestrator.log.0")
    log.touch()
    assert orch.check_service(orch.read_log_tail(log), FRESH_STATE, NOW, 60) == ("ok", "the service: last round at 2026-11-10T11:59:00Z")
    write("2026-11-10T11:59:30Z waiting: the agent's keychain is locked (the check exited 51); next try in 60 s")
    assert orch.check_service(orch.read_log_tail(log), FRESH_STATE, NOW, 60) == ("FAIL", orch.KEYCHAIN_LOCKED)


def test_main_once_runs_one_round_and_reports_a_failed_read_in_its_exit_code(capsys: pytest.CaptureFixture[str]) -> None:
    path = str(REAL_REPO / "orchestrator" / "orchestrator.example.toml")
    assert orch.main(["--config", path, "--once"], reads_of([], []), lambda s: pytest.fail("no sleep with --once"), host=FakeHost()) == 0
    assert unstamped(capsys.readouterr().out) == ["T build 0/3 · review 0 · you 0", "nothing to do"]  # the example's three slots
    assert orch.main(["--config", path, "--once"], reads_of(RuntimeError("x"), []), lambda s: None, host=FakeHost()) == 1


@pytest.mark.parametrize("args", [("--once",), ("--once", "--dry-run"), ("--preflight",), ("--check",)])
def test_start_and_check_refuse_a_retired_command_or_an_unsupported_pair(tmp_path: Path, capsys: pytest.CaptureFixture[str],
                                                                       args: tuple[str, ...]) -> None:
    path = tmp_path / "invalid.toml"
    path.write_text('run_command = ""\n' + TEST_TOML)
    assert orch.main(["--config", str(path), *args]) == 2
    assert "owlcode install" in capsys.readouterr().err
    for host in ("process",):  # herdr runs pi
        path = config_with(tmp_path, session_host=f'session_host = "{host}"', inference_profile='inference_profile = "pi-chatgpt"')
        assert orch.main(["--config", str(path), *args]) == 2
        assert (f"unsupported session host/harness pair {host}/pi; supported pairs: herdr/claude, herdr/pi, paseo/claude, paseo/pi, "
                "process/claude" in capsys.readouterr().err)
    path = config_with(tmp_path, inference_profile='inference_profile = "opencode-ollama"')  # dropped
    assert orch.main(["--config", str(path), *args]) == 2
    assert "inference_profile 'opencode-ollama' has no [profiles.opencode-ollama] table" in capsys.readouterr().err
    assert orch.main(["--config", str(tmp_path / "missing.toml"), "--once"], reads_of([], [])) == 2


# -- the real reads, with their processes replaced ------------------------------------------------------------------


def test_read_github_asks_gh_for_the_open_pull_requests_of_the_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    class Done:
        returncode, stdout, stderr = 0, '[{"number": 4}]', ""

    def fake(command: list[str], **_: Any) -> Done:
        seen.append(command)
        return Done()

    monkeypatch.setattr(orch.subprocess, "run", fake)
    assert orch.read_github(CFG) == [{"number": 4}]
    assert seen[0][:7] == ["gh", "pr", "list", "--repo", "acme/product", "--state", "open"]


def test_read_github_raises_when_gh_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    class Done:
        returncode, stdout, stderr = 1, "", "bad credentials"

    monkeypatch.setattr(orch.subprocess, "run", lambda *a, **k: Done())
    with pytest.raises(RuntimeError, match="bad credentials"):
        orch.read_github(CFG)


def test_read_linear_uses_the_modules_list_read_with_the_key_from_the_environment(tmp_path: Path) -> None:
    script = tmp_path / "fake_linear.py"
    script.write_text(
        "def api_key(environ):\n    return environ['LINEAR_API_KEY']\n"
        "def http_post(key):\n    return key\n"
        "class Linear:\n    def __init__(self, post):\n        self.post = post\n"
        "    def issues(self, team, states):\n        return [{'identifier': team + '-1', 'states': states, 'key': self.post}]\n")
    cfg = orch.load_config(example_file(tmp_path))
    cfg = type(cfg)(**{**cfg.__dict__, "linear_script": str(script)})
    found = orch.read_linear(cfg, {"LINEAR_API_KEY": "k"})
    assert found[0]["identifier"] == "ABC-1" and found[0]["key"] == "k" and "Todo" in found[0]["states"]


# -- the fix round stays in the session; the orchestrator starts one session for a fix that no session took, then escalates ---------------


ASKED_AT = "2026-11-02T10:00:00Z"
ASKED = (("CHANGES_REQUESTED", ASKED_AT),)
T0 = datetime.fromisoformat("2026-11-02T10:00:00+00:00").timestamp()


def fix_cfg(tmp_path: Path, **changes: Any) -> Any:
    base = dict(CFG.__dict__, run_command="run-it {key} {mode}", log_dir=str(tmp_path / "logs"), run_cwd=str(tmp_path))
    return type(CFG)(**{**base, **changes})


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> float:
        return self.now


class FakeWrites:
    def __init__(self) -> None:
        self.done: list[tuple[str, ...]] = []
        self.fail = False

    def writes(self) -> Any:
        def comment(key: str, body: str) -> None:
            if self.fail:
                raise RuntimeError("Linear did not answer")
            self.done.append(("comment", key, body))
        def close(number: int, body: str) -> None:
            if self.fail:
                raise RuntimeError("gh pr close failed")
            self.done.append(("close", str(number), body))
        return orch.Writes(label=lambda number, name: self.done.append(("label", str(number), name)), comment=comment,
                           move=lambda key, state: self.done.append(("move", key, state)), close=close)


def runner_of(tmp_path: Path, **changes: Any) -> tuple[Any, FakeHost, FakeWrites, Clock]:
    host, writes, clock = FakeHost(), FakeWrites(), Clock()
    return orch.Runner(fix_cfg(tmp_path, **changes), host, writes.writes(), clock), host, writes, clock


def failed_pr(number: int, branch: str, at: str) -> dict[str, Any]:
    made = pr(number, branch)
    made["statusCheckRollup"] = [{"name": "python", "status": "COMPLETED", "conclusion": "FAILURE", "startedAt": at, "completedAt": at}]
    return made


def stamp(at: float) -> str:
    return datetime.fromtimestamp(at, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def fix_comment(at: float) -> dict[str, str]:
    """The run line of a fix session that the orchestrator started (`fix_run_line`), as Linear returns it."""
    return {"created": stamp(at), "body": orch.fix_run_line("claude-sonnet-5-5", "medium", ("claude-sonnet-5-5", "high"), 7)}


def waiting_issue(*comments: dict[str, str], key: str = "ABC-7", labels: tuple[str, ...] = ()) -> dict[str, Any]:
    return dict(issue(key, state="In Progress", labels=labels), comments=list(comments))


CAUSES = {
    "changes requested": lambda: pr(7, "abc-7-x", reviews=ASKED),
    "failed": lambda: failed_pr(7, "abc-7-x", ASKED_AT),
    "conflict with main": lambda: pr(7, "abc-7-x", comments=((CONFLICT, ASKED_AT),), commit="2026-11-02T09:00:00Z"),
}


def test_a_request_for_changes_starts_no_session_before_the_wait_is_over(tmp_path: Path) -> None:
    runner, host, writes, clock = runner_of(tmp_path)
    clock.now = T0 + 60  # the session reads the verdict every minute: it has not taken the round yet
    issues, prs = [issue("ABC-7", state="In Progress")], [pr(7, "abc-7-x", reviews=ASKED)]
    assert runner.fix_round(issues, prs, dry=False) == []
    clock.now = T0 + 299
    assert runner.fix_round(issues, prs, dry=False) == []
    assert host.starts == [] and writes.done == []


def test_the_default_wait_is_five_minutes() -> None:
    assert orch.Config().claim_wait_seconds == 300 == CFG.claim_wait_seconds


def test_the_retry_waits_three_minutes_by_default() -> None:
    assert orch.Config().retry_wait_minutes == 3 == CFG.retry_wait_minutes


def test_a_fix_that_no_session_took_gets_one_new_session_for_each_cause(tmp_path: Path) -> None:
    for why, make in CAUSES.items():
        runner, host, writes, clock = runner_of(tmp_path)
        clock.now = T0 + 300
        lines = runner.fix_round([waiting_issue()], [make()], dry=False)
        assert lines == ["started a fix session for ABC-7 (claude-sonnet-5-5, medium)"], why
        assert [c for _, c, _ in host.starts] == ["run-it ABC-7 start"], why
        assert writes.done == [("comment", "ABC-7", "Run: claude-sonnet-5-5, effort medium; review claude-sonnet-5-5, effort high; profile claude (fix round of PR #7)")], why  # no escalation


def test_the_fix_session_takes_the_model_of_the_issues_labels(tmp_path: Path) -> None:
    runner, host, writes, clock = runner_of(tmp_path, run_command="run-it {key} {model} {effort}")
    clock.now = T0 + 300
    runner.fix_round([waiting_issue(labels=("needs-judgment", "intricate"))], [pr(7, "abc-7-x", reviews=ASKED)], dry=False)
    assert [c for _, c, _ in host.starts] == ["run-it ABC-7 claude-opus-5-5 high"]
    assert writes.done[0][2] == "Run: claude-opus-5-5, effort high; review claude-sonnet-5-5, effort high; profile claude (fix round of PR #7)"


def test_a_second_round_starts_no_second_session(tmp_path: Path) -> None:
    """The memory of the process alone holds when the run line has not shown in the next read of the issue."""
    runner, host, writes, clock = runner_of(tmp_path)
    prs = [pr(7, "abc-7-x", reviews=ASKED)]
    clock.now = T0 + 300
    assert len(runner.fix_round([waiting_issue()], prs, dry=False)) == 1
    for later in (T0 + 360, T0 + 500, T0 + 599):
        clock.now = later
        assert runner.fix_round([waiting_issue()], prs, dry=False) == []
    assert len(host.starts) == 1 and len(writes.done) == 1


def test_the_session_that_took_no_fix_in_a_second_wait_escalates_and_the_comment_says_one_was_started(tmp_path: Path) -> None:
    for why, make in CAUSES.items():
        runner, host, writes, clock = runner_of(tmp_path)
        clock.now = T0 + 300
        runner.fix_round([waiting_issue()], [make()], dry=False)
        writes.done.clear()
        clock.now = T0 + 300 + 299
        assert runner.fix_round([waiting_issue(fix_comment(T0 + 300))], [make()], dry=False) == [], why
        clock.now = T0 + 300 + 300
        lines = runner.fix_round([waiting_issue(fix_comment(T0 + 300))], [make()], dry=False)
        assert lines == [f"escalated ABC-7 ({why}: the session started for the fix did not take it in 5 min)"], why
        assert [d[:2] for d in writes.done] == [("label", "7"), ("comment", "ABC-7"), ("move", "ABC-7")], why
        assert writes.done[0][2] == "escalated" and writes.done[2][2] == "Escalated"
        assert "started one session for the fix" in writes.done[1][2] and "Move the issue to Todo" in writes.done[1][2] and "/implement" not in writes.done[1][2]
        assert len(host.starts) == 1, "no second start"
        runner.fix_round([waiting_issue(fix_comment(T0 + 300))], [make()], dry=False)  # the next round tries the writes again
        assert len(host.starts) == 1


def test_a_restart_between_the_start_and_the_claim_starts_no_second_session(tmp_path: Path) -> None:
    """The service restarted after the start: its table is empty, the issue's run line is the record (and the host's list)."""
    prs = [pr(7, "abc-7-x", reviews=ASKED)]
    runner, host, writes, clock = runner_of(tmp_path)  # a new process: no memory
    clock.now = T0 + 400
    assert runner.fix_round([waiting_issue(fix_comment(T0 + 300))], prs, dry=False) == []
    assert host.starts == [] and writes.done == []
    clock.now = T0 + 600
    assert runner.fix_round([waiting_issue(fix_comment(T0 + 300))], prs, dry=False)[0].startswith("escalated ABC-7 (changes requested: the session started")
    # no run line yet (the write failed), but the host lists the session as busy: still no second one
    runner, host, writes, clock = runner_of(tmp_path)
    host.spaces = [{"id": "w1", "label": "ABC-7 ·", "agent_status": "working"}]
    clock.now = T0 + 400
    assert runner.fix_round([waiting_issue()], prs, dry=False) == [] and host.starts == []


def test_a_failed_run_line_write_still_keeps_the_start(tmp_path: Path) -> None:
    runner, host, writes, clock = runner_of(tmp_path)
    writes.fail = True
    prs = [pr(7, "abc-7-x", reviews=ASKED)]
    clock.now = T0 + 300
    lines = runner.fix_round([waiting_issue()], prs, dry=False)
    assert lines == ["started a fix session for ABC-7 (claude-sonnet-5-5, medium)", "run line of ABC-7 failed: RuntimeError"]
    clock.now = T0 + 400
    assert runner.fix_round([waiting_issue()], prs, dry=False) == [] and len(host.starts) == 1


def test_a_wait_after_the_started_sessions_own_fix_escalates_with_no_second_start(tmp_path: Path) -> None:
    """The session claimed, pushed its fix and the pull request asks for changes again (a new event after the start)."""
    runner, host, writes, clock = runner_of(tmp_path)
    again = pr(7, "abc-7-x", reviews=(("CHANGES_REQUESTED", stamp(T0 + 1000)),), commit=stamp(T0 + 500))
    clock.now = T0 + 1299
    assert runner.fix_round([waiting_issue(fix_comment(T0 + 300))], [again], dry=False) == []
    clock.now = T0 + 1300
    lines = runner.fix_round([waiting_issue(fix_comment(T0 + 300))], [again], dry=False)
    assert lines == ["escalated ABC-7 (changes requested: waits again after the session started for the fix, and no session took it in 5 min)"]
    assert host.starts == [] and "the pull request waits again" in writes.done[1][2]


def test_an_escalation_ends_the_cycle_so_a_later_wait_gets_its_own_start(tmp_path: Path) -> None:
    escalation = {"created": stamp(T0 + 700), "body": orch.ESCALATION + " (changes requested) ..."}
    assert orch.fix_started_at(waiting_issue(fix_comment(T0 + 300))) == T0 + 300
    assert orch.fix_started_at(waiting_issue(fix_comment(T0 + 300), escalation)) is None
    assert orch.fix_started_at(waiting_issue({"created": stamp(T0), "body": "Run: claude-sonnet-5-5, effort medium"})) is None  # a session's own line
    runner, host, _, clock = runner_of(tmp_path)
    later = pr(7, "abc-7-x", reviews=(("CHANGES_REQUESTED", stamp(T0 + 3600)),), commit=stamp(T0 + 3000))
    clock.now = T0 + 3900
    assert runner.fix_round([waiting_issue(fix_comment(T0 + 300), escalation)], [later], dry=False) == ["started a fix session for ABC-7 (claude-sonnet-5-5, medium)"]
    assert len(host.starts) == 1


def test_no_start_beside_a_session_that_is_busy_and_it_escalates_after_two_waits(tmp_path: Path) -> None:
    runner, host, writes, clock = runner_of(tmp_path)
    host.spaces = [{"id": "w1", "label": "ABC-7 ·", "agent_status": "working"}]
    prs = [pr(7, "abc-7-x", reviews=ASKED)]
    for later in (T0 + 300, T0 + 599):
        clock.now = later
        assert runner.fix_round([waiting_issue()], prs, dry=False) == []  # it only waits: the old session may still claim
    clock.now = T0 + 600
    assert runner.fix_round([waiting_issue()], prs, dry=False) == ["escalated ABC-7 (changes requested: no session took the fix in 10 min)"]
    assert host.starts == [] and "started no session for it (a session of the key was still busy)" in writes.done[1][2]


def test_no_start_beside_a_session_of_this_process_that_the_host_does_not_list(tmp_path: Path) -> None:
    runner, host, _, clock = runner_of(tmp_path)
    runner.start_run("ABC-7", "start")  # a plain process: its session has no status in a list
    clock.now = T0 + 400
    assert runner.fix_round([waiting_issue()], [pr(7, "abc-7-x", reviews=ASKED)], dry=False) == []
    assert len(host.starts) == 1


def test_a_session_that_the_host_shows_idle_or_done_holds_no_start_back(tmp_path: Path) -> None:
    """An interactive session stays open after its run: the list says idle, and nothing in it claims the fix."""
    for status in ("idle", "done", "unknown"):
        runner, host, _, clock = runner_of(tmp_path)
        runner.start_run("ABC-7", "start")
        host.spaces = [{"id": "w1", "label": "ABC-7 ·", "agent_status": status}]
        clock.now = T0 + 400
        lines = runner.fix_round([waiting_issue()], [pr(7, "abc-7-x", reviews=ASKED)], dry=False)
        assert lines == ["started a fix session for ABC-7 (claude-sonnet-5-5, medium)"], status
        assert len(host.starts) == 2


def test_the_fix_session_takes_the_slot_of_the_keys_own_finished_session(tmp_path: Path) -> None:
    """R1-1: max_runs 2, the key's own interactive session is open and idle, another run builds: the new session replaces it."""
    runner, host, _, clock = runner_of(tmp_path, max_runs=2)
    runner.start_run("ABC-7", "start")
    host.spaces = [{"id": "w1", "label": "ABC-7 ·", "agent_status": "idle"}]
    issues = [waiting_issue(), issue("ABC-5", state="In Progress")]
    prs = [pr(7, "abc-7-x", reviews=ASKED), pr(5, "abc-5-z", draft=True)]
    assert runner.used(issues, prs) == {"ABC-7", "ABC-5"}  # the old session counts as a slot
    clock.now = T0 + 400
    assert runner.fix_round(issues, prs, dry=False) == ["started a fix session for ABC-7 (claude-sonnet-5-5, medium)"]
    assert len(host.starts) == 2


def test_no_start_when_the_session_list_cannot_be_read(tmp_path: Path) -> None:
    runner, host, writes, clock = runner_of(tmp_path)

    def broken() -> list[dict[str, Any]]:
        raise RuntimeError("herdr is not running")

    host.workspaces = broken  # type: ignore[method-assign]
    clock.now = T0 + 300
    assert runner.fix_round([waiting_issue()], [pr(7, "abc-7-x", reviews=ASKED)], dry=False) == []  # a start with no list could be a duplicate
    clock.now = T0 + 600
    assert runner.fix_round([waiting_issue()], [pr(7, "abc-7-x", reviews=ASKED)], dry=False)[0].startswith("escalated ABC-7")
    assert host.starts == [] and "the session list could not be read" in writes.done[1][2]


def test_a_fix_start_keeps_to_max_runs(tmp_path: Path) -> None:
    runner, host, writes, clock = runner_of(tmp_path, max_runs=2)
    clock.now = T0 + 300
    issues = [waiting_issue(key="ABC-7"), issue("ABC-5", state="In Progress")]
    prs = [pr(7, "abc-7-x", reviews=ASKED), pr(5, "abc-5-z", draft=True)]
    assert runner.fix_round(issues, prs, dry=False) == ["started a fix session for ABC-7 (claude-sonnet-5-5, medium)"]  # ABC-5 holds one slot
    assert [c for _, c, _ in host.starts] == ["run-it ABC-7 start"]
    # Every In Progress issue holds a slot: with ABC-8's fix round beside it, no slot is free for ABC-6's new session
    issues += [waiting_issue(key="ABC-6"), waiting_issue(key="ABC-8")]
    prs += [pr(6, "abc-6-w", reviews=ASKED), pr(8, "abc-8-y", reviews=ASKED)]
    runner, host, writes, clock = runner_of(tmp_path, max_runs=2)
    clock.now = T0 + 300
    assert runner.fix_round(issues, prs, dry=False) == [] and host.starts == []
    clock.now = T0 + 599
    assert runner.fix_round(issues, prs, dry=False) == []
    clock.now = T0 + 600
    lines = runner.fix_round(issues, prs, dry=False)
    assert [line for line in lines if "ABC-8" in line] == ["escalated ABC-8 (changes requested: no session took the fix in 10 min)"]
    assert "no slot was free" in next(d for d in writes.done if d[0] == "comment" and d[1] == "ABC-8")[2]


def test_a_hands_off_or_workflow_issue_is_never_started(tmp_path: Path) -> None:
    runner, host, writes, clock = runner_of(tmp_path)
    clock.now = T0 + 3600
    for label in ("hands-off", "workflow"):
        issues = [waiting_issue(labels=(label,))]
        assert runner.fix_round(issues, [pr(7, "abc-7-x", reviews=ASKED)], dry=False) == []
    assert host.starts == [] and writes.done == []


def test_a_fix_that_was_pushed_after_the_review_starts_nothing(tmp_path: Path) -> None:
    """the session pushed its fix and marked the pull request ready, round 2 runs, and the old review is still the
    latest. The same review with the head unchanged still gets its start."""
    issues = [waiting_issue()]
    pushed = pr(7, "abc-7-x", reviews=ASKED, commit="2026-11-02T10:11:00Z")
    runner, host, writes, clock = runner_of(tmp_path)
    clock.now = T0 + 1800
    assert orch.waiting_since(pushed, "changes requested", CFG) is None
    assert runner.fix_round(issues, [pushed], dry=False) == []
    assert writes.done == [] and host.starts == []
    unchanged = pr(7, "abc-7-x", reviews=ASKED, commit="2026-11-02T09:59:00Z")
    assert runner.fix_round(issues, [unchanged], dry=False) == ["started a fix session for ABC-7 (claude-sonnet-5-5, medium)"]


def test_failed_gates_then_a_request_for_changes_wait_from_the_review(tmp_path: Path) -> None:
    runner, host, _, clock = runner_of(tmp_path)
    both = failed_pr(7, "abc-7-x", ASKED_AT)
    both["reviews"] = [{"state": "CHANGES_REQUESTED", "submittedAt": "2026-11-02T10:20:00Z"}]
    issues = [waiting_issue()]
    clock.now = T0 + 1200 + 299
    assert runner.fix_round(issues, [both], dry=False) == []
    clock.now = T0 + 1200 + 300
    assert runner.fix_round(issues, [both], dry=False) == ["started a fix session for ABC-7 (claude-sonnet-5-5, medium)"]
    assert len(host.starts) == 1


def test_no_start_for_a_draft_a_restart_or_an_issue_out_of_in_progress(tmp_path: Path) -> None:
    runner, host, writes, clock = runner_of(tmp_path)
    clock.now = T0 + 3600
    issues = [issue("ABC-7", state="In Progress"), issue("ABC-8", state="In Progress", labels=("restart",)),
              issue("ABC-9", state="Escalated")]
    prs = [pr(7, "abc-7-x", draft=True, reviews=ASKED),  # a session took the round: its draft mark
           pr(8, "abc-8-y", reviews=ASKED), pr(9, "abc-9-z", reviews=ASKED)]
    assert runner.fix_round(issues, prs, dry=False) == []
    assert runner.used(issues, prs) == {"ABC-7", "ABC-8"}  # ABC-7's session holds a slot, and so does the restart's issue (it is In Progress)
    assert writes.done == [] and host.starts == []


def test_a_dry_run_says_what_it_would_start_and_writes_nothing(tmp_path: Path) -> None:
    runner, host, writes, clock = runner_of(tmp_path)
    clock.now = T0 + 300
    prs = [pr(7, "abc-7-x", reviews=ASKED)]
    assert runner.fix_round([waiting_issue()], prs, dry=True) == ["would start a fix session for ABC-7"]
    assert host.starts == [] and writes.done == []
    clock.now = T0 + 600  # a dry run started nothing, so the wait is still the first one
    assert runner.fix_round([waiting_issue()], prs, dry=True) == ["would start a fix session for ABC-7"]
    lines = runner.fix_round([waiting_issue(fix_comment(T0 + 300))], prs, dry=True)
    assert lines == ["would escalate ABC-7 (changes requested: the session started for the fix did not take it in 5 min)"]
    assert writes.done == []


def test_a_failed_fix_start_halts_without_spending_a_try_or_escalating(tmp_path: Path) -> None:
    runner, host, writes, clock = runner_of(tmp_path)

    def refuse(label: str, command: str, cwd: str) -> str:
        host.starts.append((label, command, cwd))
        raise RuntimeError("herdr pane run timed out")

    host.start = refuse  # type: ignore[method-assign]
    prs = [pr(7, "abc-7-x", reviews=ASKED)]
    clock.now = T0 + 300
    assert runner.fix_round([waiting_issue()], prs, dry=False) == ["start refused for ABC-7: no session created"]
    assert "host command timed out" in runner.halt.cause
    clock.now = T0 + 900
    assert runner.fix_round([waiting_issue()], prs, dry=False) == []
    assert len(host.starts) == 1 and not (runner.fix_starts or runner.tries or writes.done)


def test_a_fix_start_is_a_quiet_event_in_a_dry_run_and_prints_in_the_plan(tmp_path: Path) -> None:
    runner, _, _, clock = runner_of(tmp_path)
    clock.now = T0 + 300
    plan, events = runner.plan([waiting_issue()], [pr(7, "abc-7-x", reviews=ASKED)], dry=True)
    assert [e.kind for e in events] == ["would_start_fix"] and plan["events"][0]["event"] == "would_start_fix"
    assert "would start a fix session for ABC-7" in orch.plan_text(plan, events)
    assert [e["event"] for e in orch.shown_state(plan)["quiet"]] == ["would_start_fix"]


def test_a_later_event_gets_a_comment_of_its_own(tmp_path: Path) -> None:
    runner, _, writes, clock = runner_of(tmp_path)
    old = {"created": "2026-11-01T09:00:00Z", "body": "This issue waits for a fix (failed), and no session took it."}
    clock.now = T0 + 1800
    waiting = waiting_issue(old)  # the escalation of an earlier wait, before this review
    assert runner.fix_round([waiting], [pr(7, "abc-7-x", reviews=ASKED)], dry=False)[0].startswith("started a fix session for ABC-7")
    runner, _, writes, clock = runner_of(tmp_path)
    clock.now = T0 + 1800
    waiting = waiting_issue(old, fix_comment(T0 + 300))
    assert runner.fix_round([waiting], [pr(7, "abc-7-x", reviews=ASKED)], dry=False)[0].startswith("escalated ABC-7")
    assert [d[0] for d in writes.done] == ["label", "comment", "move"]
    assert orch.escalated_since(dict(waiting, comments=[old, {"created": "2026-11-02T10:16:00Z", "body": orch.ESCALATION + " ..."}]), T0)


def test_a_failed_move_writes_one_comment_and_stops_no_other_issue(tmp_path: Path) -> None:
    runner, _, writes, clock = runner_of(tmp_path)
    clock.now = T0 + 1800
    moves: list[str] = []

    def move(key: str, state: str) -> None:
        moves.append(key)
        if key == "ABC-7":
            raise RuntimeError("no state 'Escalated' in the team")

    def comment(key: str, body: str) -> None:
        writes.done.append(("comment", key, body))
        commented[key] = body  # Linear keeps it: the next read of the issue has it

    commented: dict[str, str] = {}
    runner.writes = orch.Writes(label=lambda number, name: None, comment=comment, move=move)
    prs = [pr(7, "abc-7-x", reviews=ASKED), pr(8, "abc-8-y", reviews=ASKED)]

    def issues() -> list[dict[str, Any]]:
        made = [waiting_issue(fix_comment(T0 + 300), key="ABC-7"), waiting_issue(fix_comment(T0 + 300), key="ABC-8")]
        for one in made:
            if one["identifier"] in commented:
                one["comments"].append({"created": "2026-11-02T10:30:00Z", "body": commented[str(one["identifier"])]})
        return made

    for resumed in (None, T0 + 1860):
        runner.resumed_at = resumed
        if resumed is not None:
            clock.now = resumed + CFG.claim_wait_seconds
        lines = runner.fix_round(issues(), prs, dry=False)
        assert lines[0] == "escalation of ABC-7 failed: RuntimeError: no state 'Escalated' in the team"
        assert lines[1].startswith("escalated ABC-8")  # ABC-7's failure does not stop ABC-8
    assert sum(1 for d in writes.done if d[1] == "ABC-7") == 1  # one comment for ABC-7 in two rounds
    assert moves.count("ABC-7") == 2  # the move is tried again


def test_a_run_by_hand_holds_a_slot(tmp_path: Path) -> None:
    runner, _, _, _ = runner_of(tmp_path, max_runs=1)
    assert runner.used([issue("ABC-5", state="In Progress")], []) == {"ABC-5"}


def test_waiting_since_reads_the_time_of_the_event(tmp_path: Path) -> None:
    assert orch.waiting_since(pr(reviews=ASKED), "changes requested", CFG) == T0
    assert orch.waiting_since(failed_pr(1, "abc-1-a", ASKED_AT), "failed", CFG) == T0
    assert orch.waiting_since(pr(reviews=(("CHANGES_REQUESTED", "not a time"),)), "changes requested", CFG) is None
    assert orch.waiting_since(pr(), "failed", CFG) is None


def test_command_for_fills_the_template_and_refuses_a_key_of_another_shape() -> None:
    assert orch.command_for('claude "/implement {key}" # {mode}', "ABC-12", "start") == 'claude "/implement ABC-12" # start'
    for key, mode in (("ABC-1; rm -rf ~", "start"), ("abc-1", "start"), ("ABC-1", "fix")):
        with pytest.raises(ValueError):
            orch.command_for("x {key}", key, mode)
    token = "20261119T230614Z-mini.local"
    assert (orch.command_for(CFG.run_command, "ABC-12", "start", "opus", "high", token=token, profile="claude", permission="auto", prompt=CFG.skill_prompt)
            == "claude --profile claude --permission-mode auto --model opus --effort high '/implement ABC-12 start 20261119T230614Z-mini.local'")
    for model in ("anthropic/claude-opus-5.5", "qwen3:32b", "claude-opus-4-1@20250805"):  # other providers' IDs
        assert model in orch.command_for(CFG.run_command, "ABC-1", "start", model, "high", token=token, profile="claude", permission="auto",
                                         prompt=CFG.skill_prompt)
    for model, effort in (("opus; rm -rf ~", "high"), ("opus", ""), ("$(x)", "high"), ("a b", "high")):  # they go into a shell too
        with pytest.raises(ValueError):
            orch.command_for(CFG.run_command, "ABC-1", "start", model, effort, token=token, profile="claude", permission="auto",
                             prompt=CFG.skill_prompt)
    for bad in ("", "x; rm -rf ~", "20261119T230614Z-$(x)", "20261119T230614Z-a b"):  # the token goes into a shell too
        with pytest.raises(ValueError):
            orch.command_for(CFG.run_command, "ABC-1", "start", "opus", "high", token=bad, profile="claude", permission="auto",
                             prompt=CFG.skill_prompt)
    assert orch.command_for("x {key}", "ABC-1", "start") == "x ABC-1"  # a template without {token} needs none


# -- the model and the effort level of a run ----------------------------------------------------------------


@pytest.mark.parametrize(("labels", "cell"), [
    ((), ("claude-sonnet-5-5", "medium", "claude")),
    (("intricate",), ("claude-sonnet-5-5", "high", "claude")),
    (("needs-judgment",), ("claude-opus-5-5", "medium", "claude")),
    (("needs-judgment", "intricate"), ("claude-opus-5-5", "high", "claude")),
])
def test_run_settings_maps_each_pair_of_difficulty_labels_to_its_cell(labels: tuple[str, ...], cell: tuple[str, str, str]) -> None:
    assert orch.run_settings(["docs", *labels, "fast-lane"], CFG) == cell


def test_an_issue_with_neither_label_gets_the_first_cell() -> None:
    assert orch.run_settings([], CFG) == (*CFG.run_cells[0], "claude") == orch.run_settings(["backend", "restart"], CFG)


def test_a_run_line_whose_write_failed_is_written_again_while_the_issue_is_in_todo(tmp_path: Path) -> None:
    # Until the claim, `.github/linear.py follow_up` reads a start only from the run line.
    runner, host, writes, _ = runner_of(tmp_path, run_command="run-it {key} {model} {effort}")
    writes.fail = True
    todo = [issue("ABC-7", area="docs")]
    assert runner.start_round(todo, [], dry=False) == ["started ABC-7 (claude-sonnet-5-5, medium)",
                                                       "run line of ABC-7 failed: RuntimeError"]
    assert runner.start_round(todo, [], dry=False) == ["run line of ABC-7 failed again: RuntimeError"]
    assert runner.start_round(todo, [], dry=False) == []  # the same error prints once
    assert runner.start_round(todo, [], dry=True) == []  # a dry round writes nothing
    writes.fail = False
    assert runner.start_round(todo, [], dry=False) == ["run line of ABC-7 written on a second try"]
    assert writes.done == [("comment", "ABC-7", "Run: claude-sonnet-5-5, effort medium; review claude-sonnet-5-5, effort high; "
                                        "profile claude")] \
        and len(host.starts) == 1
    assert runner.start_round(todo, [], dry=False) == [] and len(writes.done) == 1  # written once


def test_a_run_line_that_failed_is_dropped_when_the_issue_leaves_todo(tmp_path: Path) -> None:
    runner, _, writes, _ = runner_of(tmp_path, run_command="run-it {key} {model} {effort}")
    writes.fail = True
    runner.start_round([issue("ABC-7", area="docs")], [], dry=False)
    writes.fail = False
    claimed = issue("ABC-7", area="docs")
    claimed["state"] = "In Progress"  # the claim shows the start
    assert runner.start_round([claimed], [], dry=False) == [] and writes.done == []
    assert runner.start_round([issue("ABC-7", area="docs")], [], dry=False) == [] and writes.done == []


def test_the_config_reads_the_table_and_refuses_a_cell_of_another_shape(tmp_path: Path) -> None:
    cfg = orch.load_config(config_with(tmp_path, judgment_label="judgment_label = 'hard'", both="both = ['claude-opus-5-5', 'max']"))
    assert orch.run_settings(["hard", "intricate"], cfg) == ("claude-opus-5-5", "max", "claude")
    assert orch.run_settings(["needs-judgment"], cfg) == ("claude-sonnet-5-5", "medium", "claude")  # the label is "hard" here
    for cell in ("neither=[]", "neither='opus'", "neither=['opus', 'high; rm -rf ~']", "neither=['opus', 'high', 'x']"):
        with pytest.raises(orch.ConfigError, match=r"\[profiles.claude\] neither"):
            orch.load_config(config_with(tmp_path, neither=cell))
    # [model] alone is a cell of a harness that takes no effort level, but a profile does not mix the two shapes
    with pytest.raises(orch.ConfigError, match=r"\[profiles.claude\]: every cell on one profile names an effort level, or none does"):
        orch.load_config(config_with(tmp_path, neither="neither=['opus']"))


def test_a_start_passes_the_cell_of_the_labels_it_has_now_and_writes_the_run_line(tmp_path: Path) -> None:
    host, writes = FakeHost(), FakeWrites()
    runner = orch.Runner(fix_cfg(tmp_path, run_command="run-it {key} {model} {effort}"), host, writes.writes())
    issues = [issue("ABC-635", area="docs", labels=("needs-judgment",)), issue("ABC-645", area="infra", labels=("intricate",))]
    assert runner.start_round(issues, [], dry=False) == ["started ABC-635 (claude-opus-5-5, medium)", "started ABC-645 (claude-sonnet-5-5, high)"]
    assert [command for _, command, _ in host.starts] == ["run-it ABC-635 claude-opus-5-5 medium", "run-it ABC-645 claude-sonnet-5-5 high"]
    assert writes.done == [("comment", "ABC-635",
                            "Run: claude-opus-5-5, effort medium; review claude-sonnet-5-5, effort high; profile claude"),
                           ("comment", "ABC-645",
                            "Run: claude-sonnet-5-5, effort high; review claude-sonnet-5-5, effort high; profile claude")]
    # A run that added `intricate` and went back to Todo starts on the next cell.
    later = orch.Runner(fix_cfg(tmp_path, run_command="run-it {key} {model} {effort}"), FakeHost(), FakeWrites().writes())
    assert later.start_round([issue("ABC-635", area="docs", labels=("needs-judgment", "intricate"))], [], dry=False) == [
        "started ABC-635 (claude-opus-5-5, high)"]


def test_a_dry_run_prints_the_cell_and_writes_no_run_line(tmp_path: Path) -> None:
    host = FakeHost()
    runner = orch.Runner(fix_cfg(tmp_path), host, None)
    todo = [issue("ABC-635", area="docs", labels=("intricate",))]
    assert runner.start_round(todo, [], dry=True) == ["would start ABC-635 (claude-sonnet-5-5, high)"]
    plan, events = runner.plan(todo, [], dry=True)
    assert orch.plan_text(plan, events)[1] == "would start ABC-635 (claude-sonnet-5-5, high)"
    assert plan["events"] == [{"event": "would_start", "key": "ABC-635", "model": "claude-sonnet-5-5", "effort": "high"}]
    assert host.starts == []


def test_a_failed_run_line_keeps_the_run_and_says_so(tmp_path: Path) -> None:
    host, writes = FakeHost(), FakeWrites()
    writes.fail = True
    runner = orch.Runner(fix_cfg(tmp_path), host, writes.writes())
    assert runner.start_round([issue("ABC-635", area="docs")], [], dry=False) == [
        "started ABC-635 (claude-sonnet-5-5, medium)", "run line of ABC-635 failed: RuntimeError"]
    assert len(host.starts) == 1


# -- the start token: a recent run line of another starter is a claim ------------------------------------------

TOKEN_COMMAND = "run-it {key} start {token}"


def run_comment(body: str, at: float) -> dict[str, Any]:
    return {"body": body, "created": datetime.fromtimestamp(at, tz=UTC).isoformat()}


def todo_with(*comments: dict[str, Any], key: str = "ABC-635", related: tuple[str, ...] = ()) -> dict[str, Any]:
    return {**issue(key, area="docs", related=related), "comments": list(comments)}


def test_the_start_token_is_the_time_and_the_host_and_fits_a_shell() -> None:
    assert orch.start_token(T0, "Owner's Mac mini.local") == "20261102T100000Z-Owner-s-Mac-mini.local"
    assert orch.start_token(T0, "") == "20261102T100000Z-host"
    assert orch.TOKEN_SHAPE.fullmatch(orch.start_token(T0, "a" * 200 + "; rm -rf ~"))
    assert orch.run_line("opus", "high", token="20261102T100000Z-mini") == "Run: opus, effort high (start 20261102T100000Z-mini)"
    assert orch.run_line("opus", "high") == "Run: opus, effort high"
    # the local review's model and effort level go on the line, so `measure` groups the reviews by the model that
    # made them; a profile whose file names none (`run_line`'s own default, a test) writes no such part
    assert orch.run_line("opus", "medium", ("claude-sonnet-5-5", "high"), "tok") == (
        "Run: opus, effort medium; review claude-sonnet-5-5, effort high (start tok)")
    assert orch.run_line("opus", "medium", ("glm-5.3", "")) == "Run: opus, effort medium; review glm-5.3, effort none"


def test_pending_start_reads_only_the_newest_run_line_with_a_token_inside_the_window() -> None:
    line = "Run: opus, effort high (start 20261102T095800Z-other)"
    assert orch.pending_start(todo_with(run_comment(line, T0 - 120)), T0, 600) == ("20261102T095800Z-other", T0 - 120)
    assert orch.pending_start(todo_with(run_comment(line, T0 - 600)), T0, 600) is None  # old: a dead start elsewhere
    assert orch.pending_start(todo_with(run_comment("Run: opus, effort high", T0 - 60)), T0, 600) is None  # before the start token
    assert orch.pending_start(todo_with(), T0, 600) is None
    # a session's own run line (no token) after the start's: the start was claimed (and the issue given back since)
    claimed = todo_with(run_comment("Run: opus, effort unknown", T0 - 60), run_comment(line, T0 - 120))
    assert orch.pending_start(claimed, T0, 600) is None
    # a fix round's line has no token either; a comment that is no run line counts for nothing
    assert orch.pending_start(todo_with(run_comment(line, T0 - 120), run_comment("Draft stack on ABC-1", T0 - 10)), T0, 600) is not None


def test_a_recent_run_line_of_another_starter_holds_the_start_and_its_related_links(tmp_path: Path) -> None:
    runner, host, writes, clock = runner_of(tmp_path, run_command=TOKEN_COMMAND)
    elsewhere = todo_with(run_comment("Run: claude-opus-5-5, effort high (start 20261102T095730Z-other)", T0 - 150))
    near = issue("ABC-636", area="frontend", related=("ABC-635",))
    assert runner.start_round([elsewhere, near], [], dry=False) == [
        "ABC-635 waits: started elsewhere at 09:57 UTC, not claimed yet", "ABC-636 waits: related to ABC-635, which runs"]
    assert host.starts == [] and writes.done == []
    plan, _ = runner.plan([elsewhere], [], dry=True)
    assert {"key": "ABC-635", "reason": "started elsewhere at 09:57 UTC, not claimed yet"} in [
        *plan["other_waits"], *({"key": i["key"], "reason": i.get("reason")} for q in plan["areas"] for i in q["queue"])]
    clock.now += 60  # the reason names the start's time, so a later round shows the same plan and the loop prints nothing new
    assert orch.shown_state(runner.plan([elsewhere], [], dry=True)[0]) == orch.shown_state(plan)
    clock.now += 390  # 600 s after the other start: its session never claimed, so this one starts
    assert runner.start_round([elsewhere], [], dry=False) == ["started ABC-635 (claude-sonnet-5-5, medium)"]


def test_a_start_writes_its_token_on_the_run_line_and_gives_the_session_the_same_one(tmp_path: Path) -> None:
    runner, host, writes, _ = runner_of(tmp_path, run_command=TOKEN_COMMAND)
    runner.host_name = "mini"
    assert runner.start_round([todo_with()], [], dry=False) == ["started ABC-635 (claude-sonnet-5-5, medium)"]
    assert [command for _, command, _ in host.starts] == ["run-it ABC-635 start 20261102T100000Z-mini"]
    assert writes.done == [("comment", "ABC-635",
                            "Run: claude-sonnet-5-5, effort medium; review claude-sonnet-5-5, effort high; profile claude "
                            "(start 20261102T100000Z-mini)")]
    # A run_command without {token} gives the session no token: its run line has none, or the session would stop at its own start.
    plain, _, plain_writes, _ = runner_of(tmp_path)
    plain.start_round([todo_with()], [], dry=False)
    assert plain_writes.done == [("comment", "ABC-635", "Run: claude-sonnet-5-5, effort medium; review claude-sonnet-5-5, "
                                  "effort high; profile claude")]


def test_its_own_run_line_does_not_hold_the_retry_of_an_unclaimed_start(tmp_path: Path) -> None:
    runner, host, writes, clock = runner_of(tmp_path, run_command=TOKEN_COMMAND)
    assert runner.start_round([todo_with()], [], dry=False) == ["started ABC-635 (claude-sonnet-5-5, medium)"]
    own = todo_with(run_comment(writes.done[-1][2], clock.now))
    host.ended.add("s1")  # the session ended before its claim
    clock.now += 121
    assert runner.start_round([own], [], dry=False) == ["started ABC-635 (claude-sonnet-5-5, medium)"]
    assert len(host.starts) == 2 and host.starts[0][1] != host.starts[1][1]  # each start has its own token
    # After a restart the process knows no token of its own: its earlier start waits like another's, for the window at most.
    restarted = orch.Runner(runner.cfg, FakeHost(), FakeWrites().writes(), clock)
    assert restarted.start_round([own], [], dry=False) == ["ABC-635 waits: started elsewhere at 10:00 UTC, not claimed yet"]


def test_a_failed_run_line_write_starts_as_before_and_writes_the_token_later(tmp_path: Path) -> None:
    runner, host, writes, _ = runner_of(tmp_path, run_command=TOKEN_COMMAND)
    runner.host_name = "mini"
    writes.fail = True
    assert runner.start_round([todo_with()], [], dry=False) == [
        "started ABC-635 (claude-sonnet-5-5, medium)", "run line of ABC-635 failed: RuntimeError"]
    assert len(host.starts) == 1
    # No line on the issue, so no guard: another orchestrator starts it as before.
    other, other_host, _, _ = runner_of(tmp_path, run_command=TOKEN_COMMAND)
    assert other.start_round([todo_with()], [], dry=False) == ["started ABC-635 (claude-sonnet-5-5, medium)"]
    assert len(other_host.starts) == 1
    writes.fail = False  # the next round writes the same line, with the token that the session got
    assert runner.write_again([todo_with()]) == [orch.Event("run_line_written", "ABC-635", "run line of ABC-635 written on a second try")]
    assert writes.done == [("comment", "ABC-635",
                            "Run: claude-sonnet-5-5, effort medium; review claude-sonnet-5-5, effort high; profile claude "
                            "(start 20261102T100000Z-mini)")]


def test_check_warns_when_run_command_gives_no_token() -> None:
    assert orch.check_token(dataclasses.replace(CFG, run_command=TOKEN_COMMAND))[0] == orch.OK
    level, text = orch.check_token(dataclasses.replace(CFG, run_command="run-it {key}"))
    assert level == orch.WARN and "{token}" in text
    assert orch.check_token(EXAMPLE)[0] == orch.OK  # the example gives one, through the harness's skill_prompt
    no_token = dataclasses.replace(EXAMPLE, skill_prompt="/implement {key}")
    assert orch.check_token(no_token)[0] == orch.WARN


@pytest.mark.real_clock("a real process runs the fake run_command and the test waits for it")
def test_the_process_host_runs_the_command_detached_and_reports_its_end(tmp_path: Path) -> None:
    recorder = tmp_path / "record.sh"
    recorder.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$(dirname "$0")/args.txt"\n'
                        'echo \'{"type":"system","subtype":"init"}\'\necho done\nexit 1\n')
    recorder.chmod(0o755)
    host = orch.ProcessHost(tmp_path / "logs")
    runner = orch.Runner(fix_cfg(tmp_path, run_command=f"{recorder} {{key}} {{mode}}"), host)
    runner.start_run("ABC-7", "start")
    session = runner.live["ABC-7"].session
    host.procs[session].wait(timeout=30)
    assert not host.alive(session)
    assert (tmp_path / "args.txt").read_text().split() == ["ABC-7", "start"]
    assert host.tail(session).splitlines()[-1] == "done"
    assert host.procs[session].returncode == 1  # an error after init still counts as a session
    with pytest.raises(RuntimeError, match="exited before reporting its session"):
        host.start("ABC-8 ·", "/bin/sh -c 'exit 1'", str(tmp_path))
    assert len(host.procs) == 1


def test_the_process_host_cannot_spawn_without_a_log_reader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    open_file = Path.open

    def open_log(path: Path, mode: str = "r", *args: Any, **kwargs: Any) -> Any:
        if mode == "rb":
            raise OSError("log not readable")
        return open_file(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_log)
    monkeypatch.setattr(orch.subprocess, "Popen", lambda *a, **kw: pytest.fail("spawned without a reader"))
    with pytest.raises(OSError, match="log not readable"):
        orch.ProcessHost(tmp_path).start("ABC-7 ·", "claude", str(tmp_path))


def test_the_herdr_host_opens_a_workspace_runs_the_command_and_reads_its_state() -> None:
    calls: list[list[str]] = []
    replies = {
        "create": '{"result": {"root_pane": {"pane_id": "w2:p1"}, "workspace": {"workspace_id": "w2"}}}',
        "start": "{}",
        "process-info": '{"result": {"process_info": {"foreground_process_group_id": 5, "shell_pid": 4}}}',
    }

    def run(argv: Any) -> Any:
        calls.append(list(argv))
        return orch.subprocess.CompletedProcess(argv, 0, replies.get(argv[2], "out"), "")

    host = orch.HerdrHost(run)
    assert host.start("abc-7-fix", "claude --profile claude '/implement ABC-7'", "/clone") == "w2:p1"
    assert calls[0] == ["herdr", "workspace", "create", "--label", "abc-7-fix", "--cwd", "/clone", "--no-focus"]
    assert calls[1] == ["herdr", "agent", "start", "orchestrator-w2-p1", "--kind", "claude", "--pane", "w2:p1", "--",
                        "--profile", "claude", "/implement ABC-7"]
    assert host.alive("w2:p1")
    replies["process-info"] = '{"result": {"process_info": {"foreground_process_group_id": 4, "shell_pid": 4}}}'
    assert not host.alive("w2:p1")  # back at its shell: the command ended


@pytest.mark.parametrize("agent", [None, "claude"])
def test_the_herdr_host_closes_the_workspace_only_if_there_is_no_agent(agent: str | None) -> None:
    calls = []

    def run(argv: Any) -> Any:
        calls.append(list(argv))
        if argv[2] == "start":
            return orch.subprocess.CompletedProcess(argv, 1, "", "agent start did not confirm readiness")
        reply = {"root_pane": {"pane_id": "w2:p1"}, "workspace": {"workspace_id": "w2"}, "pane": {"agent": agent}}
        return orch.subprocess.CompletedProcess(argv, 0, json.dumps({"result": reply}), "")

    host = orch.HerdrHost(run)
    if agent:
        assert host.start("ABC-7 ·", "claude", "/clone") == "w2:p1"
        assert calls[-1] == ["herdr", "pane", "get", "w2:p1"]
    else:
        with pytest.raises(RuntimeError, match="did not confirm readiness"):
            host.start("ABC-7 ·", "claude", "/clone")
        assert calls[-1] == ["herdr", "workspace", "close", "w2"]


def test_herdr_defers_cleanup_until_it_can_check_the_new_pane() -> None:
    codes = {"start": 1, "get": 1, "list": 1}
    calls = []

    def run(argv: Any) -> Any:
        calls.append(argv)
        reply = {"root_pane": {"pane_id": "w2:p1"}, "workspace": {"workspace_id": "w2"}, "pane": {}}
        return orch.subprocess.CompletedProcess(argv, codes.get(argv[2], 0), json.dumps({"result": reply}), "")

    with pytest.raises(orch.LaunchUnknown) as exc:
        orch.HerdrHost(run).start("ABC-7 ·", "claude", "/clone")
    assert all(call[2] != "close" for call in calls)
    codes["get"] = 0
    assert exc.value.confirm() is None
    assert calls[-1] == ["herdr", "workspace", "close", "w2"]


def test_a_start_that_raised_does_not_hold_a_slot(tmp_path: Path) -> None:
    class Failing(FakeHost):
        def start(self, label: str, command: str, cwd: str) -> str:
            raise RuntimeError("herdr pane run failed")

    clock = Clock()
    runner = orch.Runner(fix_cfg(tmp_path), Failing(), None, clock)
    with pytest.raises(RuntimeError):
        runner.start_run("ABC-7", "start")
    assert not (runner.last_start or runner.live)


def test_the_herdr_host_reads_a_closed_pane_as_ended() -> None:
    def run(argv: Any) -> Any:
        return orch.subprocess.CompletedProcess(argv, 1, "", "no such pane")

    assert not orch.HerdrHost(run).alive("w9:p9")


def test_config_checks_the_session_host_and_the_numbers(tmp_path: Path) -> None:
    for line, words in (("session_host='ssh'", "session_host"), ("claim_grace_seconds=-1", "claim_grace_seconds"),
                        ("areas=[]", "distinct_areas is on, but \\[linear\\] areas is empty")):
        with pytest.raises(orch.ConfigError, match=words):
            orch.load_config(config_with(tmp_path, **{line.split("=")[0]: line}))
    assert CFG.session_host == "herdr" and CFG.claim_grace_seconds == 120 and CFG.claim_wait_seconds == 300
    assert CFG.run_command == 'claude --profile {profile}{review_flag}{workers_flag} --permission-mode {permission} --model {model} --effort {effort} {prompt}' and CFG.skill_prompt == "/implement {key} start {token}"
    assert CFG.escalated == "Escalated" and CFG.escalated_label == "escalated"


@pytest.mark.time_machine(datetime.fromisoformat("2026-11-02T11:00:00+00:00"))
def test_main_starts_a_fix_session_with_dry_run_off_and_only_prints_with_dry_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = tmp_path / "c.toml"
    config.write_text(TEST_TOML.replace("dry_run = true", "dry_run = false"))
    reads = reads_of([issue("ABC-7", state="In Progress")], [pr(7, "abc-7-x", reviews=ASKED)])
    writes, host = FakeWrites(), FakeHost()
    # A fix start runs the setup preflight: stub its probes, so the test reads no real keychain (on Linux the
    # `security` command is absent and the preflight refuses the start).
    assert orch.main(["--config", str(config), "--once"], reads, host=host, writes=writes.writes(), probes=probes()) == 0
    assert "started a fix session for ABC-7 (claude-sonnet-5-5, medium)" in capsys.readouterr().out
    assert writes.done == [("comment", "ABC-7", "Run: claude-sonnet-5-5, effort medium; review claude-sonnet-5-5, effort high; profile claude; pytest workers 7 (fix round of PR #7)")] and len(host.starts) == 1
    # --dry-run wins over the config
    writes2, host2 = FakeWrites(), FakeHost()
    assert orch.main(["--config", str(config), "--once", "--dry-run"], reads, host=host2, writes=writes2.writes(),
                     probes=probes()) == 0
    assert writes2.done == [] and host2.starts == [] and "would start a fix session for ABC-7" in capsys.readouterr().out


@pytest.mark.time_machine(datetime.fromisoformat("2026-11-02T11:00:00+00:00"))
def test_main_escalates_a_fix_that_its_session_did_not_take(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = tmp_path / "c.toml"
    config.write_text(TEST_TOML.replace("dry_run = true", "dry_run = false"))
    started = {"created": "2026-11-02T10:30:00Z", "body": "Run: claude-sonnet-5-5, effort medium; review claude-sonnet-5-5, effort high (fix round of PR #7)"}
    reads = reads_of([dict(issue("ABC-7", state="In Progress"), comments=[started])], [pr(7, "abc-7-x", reviews=ASKED)])
    writes, host = FakeWrites(), FakeHost()
    assert orch.main(["--config", str(config), "--once"], reads, host=host, writes=writes.writes()) == 0
    assert "escalated ABC-7 (changes requested: the session started for the fix did not take it in 30 min)" in capsys.readouterr().out
    assert [d[0] for d in writes.done] == ["label", "comment", "move"] and host.starts == []


# -- the start from Todo ---------------------------------------------------------------------------------------


def test_start_round_starts_the_picks_with_the_start_mode(tmp_path: Path) -> None:
    runner, host, _, _ = runner_of(tmp_path)
    issues = [issue("ABC-635", size="XS", area="docs"), issue("ABC-602", priority="low", area="docs"), issue("ABC-645", area="frontend")]
    assert runner.start_round(issues, [], dry=False) == ["started ABC-635 (claude-sonnet-5-5, medium)", "started ABC-645 (claude-sonnet-5-5, medium)", "ABC-602 waits: area docs: ABC-635 runs"]
    assert [c for _, c, _ in host.starts] == ["run-it ABC-635 start", "run-it ABC-645 start"]


def test_start_round_in_a_dry_run_starts_nothing(tmp_path: Path) -> None:
    runner, host, _, _ = runner_of(tmp_path)
    assert runner.start_round([issue("ABC-635", area="docs")], [], dry=True) == ["would start ABC-635 (claude-sonnet-5-5, medium)"]
    assert host.starts == []


def _space(key: str, status: str) -> dict[str, Any]:
    return {"id": f"w-{key}", "label": f"{key} ·", "agent_status": status}


def test_todo_with_an_idle_session_wakes_it_once_and_starts_none(tmp_path: Path) -> None:
    runner, host, _, _ = runner_of(tmp_path)
    host.spaces = [_space("ABC-635", "idle")]
    host.wakeable = True
    todo = [issue("ABC-635", area="docs")]
    assert runner.start_round(todo, [], dry=False) == ["ABC-635: Todo again, woke its idle session"]
    assert host.wakes == [("ABC-635", orch.TODO_WAKE_TEXT)] and host.starts == []
    assert runner.start_round(todo, [], dry=False) == [f"ABC-635 waits: {orch.TODO_WOKEN_WAIT}"] and len(host.wakes) == 1 and host.starts == []
    runner.start_round([issue("ABC-635", state="In Progress")], [], dry=False)  # it left Todo: the next move wakes again
    runner.start_round(todo, [], dry=False)
    assert len(host.wakes) == 2


def test_todo_with_a_done_session_or_none_starts_once(tmp_path: Path) -> None:
    for spaces in ([_space("ABC-635", "done")], []):
        runner, host, _, _ = runner_of(tmp_path)
        host.spaces = spaces
        assert runner.start_round([issue("ABC-635", area="docs")], [], dry=False)[0].startswith("started ABC-635")
        assert host.wakes == [] and len(host.starts) == 1


def test_a_wake_that_finds_no_session_lets_the_start_go_on_in_the_next_round(tmp_path: Path) -> None:
    runner, host, _, _ = runner_of(tmp_path)
    host.spaces = [_space("ABC-635", "idle")]
    todo = [issue("ABC-635", area="docs")]
    assert runner.start_round(todo, [], dry=False) == [] and host.starts == []  # host.wake answered False
    assert runner.start_round(todo, [], dry=False)[0].startswith("started ABC-635") and len(host.wakes) == 1


def test_a_wake_that_raises_gives_one_event_and_the_start_goes_on_in_the_next_round(tmp_path: Path) -> None:
    runner, host, _, _ = runner_of(tmp_path)
    host.spaces = [_space("ABC-635", "idle")]
    host.wake_error = RuntimeError("host down")
    todo = [issue("ABC-635", area="docs")]
    first = runner.start_round(todo, [], dry=False)
    assert len(first) == 1 and first[0].startswith("wake of the session of ABC-635 failed: RuntimeError") and host.starts == []
    assert runner.start_round(todo, [], dry=False)[0].startswith("started ABC-635") and len(host.wakes) == 1


def test_an_idle_session_of_a_paused_profile_gets_no_wake(tmp_path: Path) -> None:
    runner, host, _, _ = runner_of(tmp_path)
    host.spaces = [_space("ABC-635", "idle")]
    host.wakeable = True
    runner.stops.pause = {None: ("ABC-9", "the usage limit")}  # `Stops.events` fills it from the host, once for each round
    lines = runner.start_round([issue("ABC-635", area="docs")], [], dry=False)
    assert host.wakes == [] and host.starts == [] and not any("woke" in line for line in lines)


def test_an_idle_session_gets_no_wake_while_its_start_would_wait(tmp_path: Path) -> None:
    """R1-1: the wake follows every guard of a start (`max_runs`, the Area, a related run), and the woken key holds its slot."""
    runner, host, _, _ = runner_of(tmp_path, max_runs=1)
    host.spaces = [_space("ABC-635", "idle")]
    host.wakeable = True
    lines = runner.start_round([issue("ABC-1", state="In Progress", area="docs"), issue("ABC-635", area="docs")], [], dry=False)
    assert host.wakes == [] and host.starts == [] and lines == ["ABC-635 waits: area docs: ABC-1 runs"]
    runner, host, _, _ = runner_of(tmp_path, max_runs=1)  # a woken key holds the one slot
    host.spaces = [_space("ABC-635", "idle")]
    host.wakeable = True
    lines = runner.start_round([issue("ABC-635", area="docs"), issue("ABC-7", priority="low", area="frontend")], [], dry=False)
    assert lines[0] == "ABC-635: Todo again, woke its idle session" and len(host.wakes) == 1 and host.starts == []
    # Woken, idle again, still in Todo: the next round says why the key holds the slot.
    host.spaces = [_space("ABC-635", "idle")]
    lines = runner.start_round([issue("ABC-635", area="docs"), issue("ABC-7", priority="low", area="frontend")], [], dry=False)
    assert lines[0] == f"ABC-635 waits: {orch.TODO_WOKEN_WAIT}" and len(host.wakes) == 1 and host.starts == [], lines


def test_a_dry_round_says_it_would_wake_the_idle_session(tmp_path: Path) -> None:
    runner, host, _, _ = runner_of(tmp_path)
    host.spaces = [_space("ABC-635", "idle")]
    assert runner.start_round([issue("ABC-635", area="docs")], [], dry=True) == ["ABC-635: Todo again, would wake its idle session"]
    assert host.wakes == [] and host.starts == []


def test_a_started_issue_is_not_started_again_while_linear_still_shows_it_in_todo(tmp_path: Path) -> None:
    runner, host, _, clock = runner_of(tmp_path)
    todo = [issue("ABC-635", area="docs"), issue("ABC-602", priority="low", area="docs")]
    runner.start_round(todo, [], dry=False)
    clock.now += 30  # Linear still says Todo for ABC-635: its claim has not landed
    assert runner.start_round(todo, [], dry=False) == ["ABC-602 waits: area docs: ABC-635 runs"]
    assert len(host.starts) == 1
    host.ended.add("s1")
    clock.now += 200  # past the grace time, the session gone, and the issue still in Todo: it starts again
    assert runner.start_round(todo, [], dry=False)[0] == "started ABC-635 (claude-sonnet-5-5, medium)"


def test_an_issue_in_review_with_its_pull_request_open_holds_its_area(tmp_path: Path) -> None:
    runner, _, _, _ = runner_of(tmp_path)
    issues = [issue("ABC-9", state="In Review", area="docs"), issue("ABC-635", area="docs"), issue("ABC-8", state="Approved", area="infra"),
              issue("ABC-645", area="infra")]
    assert runner.start_round(issues, [pr(9, "abc-9-y")], dry=True) == [
        "ABC-635 waits: area docs: ABC-9 runs", "would start ABC-645 (claude-sonnet-5-5, medium)"]  # ABC-8 has no open pull request: it holds nothing


def test_an_issue_in_escalated_holds_no_area_but_holds_its_related_links(tmp_path: Path) -> None:
    """an issue in Escalated waits for the owner, often overnight, so its Area's queue goes on; its pull request
    still touches the files of a related issue."""
    runner, _, _, _ = runner_of(tmp_path, max_runs=3)
    issues = [issue("ABC-9", state="Escalated", area="docs", related=("ABC-646",)), issue("ABC-635", area="docs"),
              issue("ABC-646", area="frontend"), issue("ABC-647", area="infra", related=("ABC-9",))]
    assert runner.start_round(issues, [pr(9, "abc-9-y")], dry=True) == [
        "would start ABC-635 (claude-sonnet-5-5, medium)", "ABC-646 waits: related to ABC-9, which waits for the owner",
        "ABC-647 waits: related to ABC-9, which waits for the owner"]
    # with no open pull request it holds nothing (a question for the owner)
    assert runner.start_round(issues, [], dry=True) == ["would start ABC-635 (claude-sonnet-5-5, medium)", "would start ABC-646 (claude-sonnet-5-5, medium)", "would start ABC-647 (claude-sonnet-5-5, medium)"]
    # the same issue in In Review still holds its Area
    issues[0] = dict(issues[0], state="In Review")
    assert runner.start_round(issues, [pr(9, "abc-9-y")], dry=True)[0] == "ABC-635 waits: area docs: ABC-9 runs"


def test_an_issue_this_process_started_holds_no_area_in_escalated_while_its_session_lives(tmp_path: Path) -> None:
    """An interactive session stays alive after its run ends at a question for the owner."""
    runner, host, _, clock = runner_of(tmp_path, max_runs=3)
    assert runner.start_round([issue("ABC-9", area="docs")], [], dry=False) == ["started ABC-9 (claude-sonnet-5-5, medium)"]
    clock.now += CFG.claim_grace_seconds + 1
    assert host.alive("s1")
    issues = [issue("ABC-9", state="Escalated", area="docs"), issue("ABC-635", area="docs")]
    assert runner.start_round(issues, [pr(9, "abc-9-y")], dry=True) == ["would start ABC-635 (claude-sonnet-5-5, medium)"]
    # in In Progress (the owner's answer, a fix round) it holds its Area again
    issues[0] = dict(issues[0], state="In Progress")
    assert runner.start_round(issues, [pr(9, "abc-9-y")], dry=True) == ["ABC-635 waits: area docs: ABC-9 runs"]


def test_the_slots_count_the_in_progress_issues(tmp_path: Path) -> None:
    runner, _, _, _ = runner_of(tmp_path)
    issues = [issue("ABC-5", state="In Progress", area="backend"), issue("ABC-6", state="In Progress", area="frontend"),
              issue("ABC-635", area="docs")]
    assert runner.start_round(issues, [], dry=True) == ["ABC-635 waits: no free slot"]


def test_a_halt_stops_all_starts_but_keeps_wakes_closes_and_the_queue(tmp_path: Path) -> None:
    class Failing(FakeHost):
        def start(self, label: str, command: str, cwd: str) -> str:
            if "ABC-635" in command and not ready:
                raise RuntimeError('Model not found; command printed private-key-value')
            return super().start(label, command, cwd)

    runner, _, writes, clock = runner_of(tmp_path)
    host = runner.host = Failing()
    states, probes, notices, log = [], [], [], []
    ready = False
    fail_read = False
    runner.halt.check = lambda: (probes.append(clock.now) or [(orch.OK if ready else orch.FAIL, "preflight")])
    runner.writes = dataclasses.replace(writes.writes(), notice=lambda cause, at: notices.append(cause))
    runner.queue = orch.PinnedComment("ABC-99", "Queue", 600, None)
    todo = [issue("ABC-635", area="docs"), issue("ABC-645", area="frontend")]
    path = tmp_path / "state.json"
    host.wakeable = True

    def read() -> list[dict[str, Any]]:
        if fail_read:
            raise OSError("read failed after recovery")
        return states

    def read_prs() -> list[dict[str, Any]]:
        return [pr(7, "abc-7-x", reviews=ASKED), *([pr(8, "abc-8-x")] if clock.now > T0 else [])]

    data = orch.Reads(read, read_prs, final=lambda key: ("Canceled" if key == "ABC-8" else "Done", T0 - 100_000, ()))
    states[:] = todo
    cfg2 = dataclasses.replace(runner.cfg, team="XYZ")
    runner2 = orch.Runner(cfg2, host, writes.writes(), clock, halt=runner.halt)
    teams = [orch.Team(runner.cfg, data, runner), orch.Team(cfg2, orch.Reads(lambda: [issue("XYZ-1", area="docs")], list), runner2)]

    def poll() -> bool:
        return orch.round_all(teams, log.append, dry=False, state_file=path)

    assert poll()
    plan = json.loads(path.read_text())
    banner = f"halted: {plan['halted']}"
    assert "model not found" in banner and "private-key-value" not in "\n".join(log) + path.read_text()
    assert banner in log and runner.queue.lines[0] == banner
    assert orch.render_all(plan, T0, 200, 20, False)[0] == banner
    assert not (host.starts or writes.done or runner.tries)
    assert notices == [plan["halted"]]

    states[:] = [*todo, waiting_issue()]
    host.spaces = [{"workspace_id": "finished", "label": "ABC-89 ·", "agent_status": "done"}]
    clock.now = T0 + 60
    assert poll()
    assert host.wakes[0][0] == "ABC-7" and host.closed == ["finished"]
    assert not (host.starts or probes)
    assert writes.done == [("close", "8", "Closed: ABC-8 was canceled")]
    clock.now = T0 + 300
    assert poll()
    assert probes == [clock.now] and len(notices) == 1 and not host.starts
    ready = fail_read = True
    clock.now = T0 + 600
    assert not poll()
    recovered = json.loads(path.read_text())
    assert recovered["failed"] and "halted" not in recovered
    assert notices[-1] is None and len(notices) == 2 and len(host.wakes) == 1
    assert [start[0] for start in host.starts] == ["XYZ-1 ·"]
    assert not any("halted:" in line for line in runner.queue.lines)


@pytest.mark.parametrize("fix", [False, True])
def test_a_preflight_that_passes_does_not_report_the_same_launch_failure_again(tmp_path: Path, fix: bool) -> None:
    class Failing(FakeHost):
        def start(self, label: str, command: str, cwd: str) -> str:
            attempts.append(label)
            if error:
                raise RuntimeError(error)
            return super().start(label, command, cwd)

    runner, _, writes, clock = runner_of(tmp_path)
    runner.host = Failing()
    attempts, notices, lines = [], [], []
    error = "host command timed out"
    runner.halt.check = lambda: [(orch.OK, "preflight")]
    runner.writes = dataclasses.replace(writes.writes(), notice=lambda cause, at: notices.append(cause))
    data = (orch.Reads(lambda: [waiting_issue()], lambda: [pr(7, "abc-7-x", reviews=ASKED)])
            if fix else orch.Reads(lambda: [issue("ABC-7", area="docs")], list))
    clock.now += runner.cfg.claim_wait_seconds

    for _ in range(3):
        assert orch.round_once(runner.cfg, data, lines.append, runner, dry=False)
        assert runner.halt.cause and not runner.tries
        clock.now += 60 * runner.cfg.halt_check_minutes
    assert len(attempts) == 3 and len(notices) == 1 and not writes.done
    assert not any(line.startswith("resumed at ") for line in lines)

    error = "model not found"
    assert orch.round_once(runner.cfg, data, lines.append, runner, dry=False)
    assert len(notices) == 2 and notices[-1].endswith(error)

    error = ""
    clock.now += 60 * runner.cfg.halt_check_minutes
    assert orch.round_once(runner.cfg, data, lines.append, runner, dry=False)
    assert notices[-1] is None and not runner.halt.reported


def test_an_agent_in_error_before_its_claim_halts_and_counts_no_try(tmp_path: Path) -> None:
    """a Paseo agent that stops in error within the claim wait, before any claim, is a setup failure: the halt
    stops the starts, the agent is archived, the notice names the cause, and no try is counted."""
    runner, host, writes, clock = runner_of(tmp_path, session_host="paseo", run_command="paseo run --no-wait --quiet x")
    notices = []
    runner.writes = dataclasses.replace(writes.writes(), notice=lambda cause, at: notices.append(cause))
    data = orch.Reads(lambda: [issue("ABC-7", area="docs")], list)
    state = tmp_path / "state.json"
    assert orch.round_once(runner.cfg, data, [].append, runner, dry=False, state_file=state)
    assert runner.tries == {"ABC-7": 1} and not runner.halt.cause

    host.failed_sessions.add("s1")
    clock.now += 60
    assert orch.round_once(runner.cfg, data, [].append, runner, dry=False, state_file=state)
    cause = "paseo start failed for ABC-7 (claude-sonnet-5-5): the agent stopped in error before its claim"
    assert runner.halt.cause == cause and not runner.tries
    assert host.closed == ["s1"] and notices == [cause]
    assert json.loads(state.read_text())["halted"] == cause

    clock.now += 60
    assert orch.round_once(runner.cfg, data, [].append, runner, dry=False, state_file=state)
    assert len(host.starts) == 1 and runner.halt.cause == cause, "a halted setup starts nothing"
    runner.halt.check = lambda: [(orch.OK, "preflight")]
    clock.now += 60 * runner.cfg.halt_check_minutes
    assert orch.round_once(runner.cfg, data, [].append, runner, dry=False, state_file=state)
    assert not runner.halt.cause and len(host.starts) == 2, "the fixed setup starts the issue again"


def test_a_repeated_agent_error_keeps_one_notice_and_the_halt(tmp_path: Path) -> None:
    """A passing preflight keeps the notice open until the restarted start claims or its claim wait passes,
    so the same error coming back halts with no new notice and no resumed/closed notice in between."""
    runner, host, writes, clock = runner_of(tmp_path, session_host="paseo", run_command="paseo run --no-wait --quiet x")
    notices = []
    runner.writes = dataclasses.replace(writes.writes(), notice=lambda cause, at: notices.append(cause))
    data = orch.Reads(lambda: [issue("ABC-7", area="docs")], list)
    state = tmp_path / "state.json"
    assert orch.round_once(runner.cfg, data, [].append, runner, dry=False, state_file=state)
    host.failed_sessions.add("s1")
    clock.now += 60
    assert orch.round_once(runner.cfg, data, [].append, runner, dry=False, state_file=state)
    cause = "paseo start failed for ABC-7 (claude-sonnet-5-5): the agent stopped in error before its claim"
    assert notices == [cause] and runner.halt.cause == cause

    runner.halt.check = lambda: [(orch.OK, "preflight")]
    clock.now += 60 * runner.cfg.halt_check_minutes
    assert orch.round_once(runner.cfg, data, [].append, runner, dry=False, state_file=state)
    assert runner.halt.cause is None and len(host.starts) == 2, "the passing preflight starts the issue again"
    assert notices == [cause], "the notice stays open while the restarted start has not claimed"

    host.failed_sessions.add("s2")
    clock.now += 60
    assert orch.round_once(runner.cfg, data, [].append, runner, dry=False, state_file=state)
    assert runner.halt.cause == cause and notices == [cause], "the same error keeps the halt and its one notice"
    clock.now += 60
    assert orch.round_once(runner.cfg, data, [].append, runner, dry=False, state_file=state)
    assert len(host.starts) == 2 and notices == [cause]


def test_a_restarted_agent_that_claims_closes_the_notice(tmp_path: Path) -> None:
    """Once the restarted start claims, the halt may close its notice as before."""
    runner, host, writes, clock = runner_of(tmp_path, session_host="paseo", run_command="paseo run --no-wait --quiet x")
    notices = []
    runner.writes = dataclasses.replace(writes.writes(), notice=lambda cause, at: notices.append(cause))
    data = orch.Reads(lambda: [issue("ABC-7", area="docs")], list)
    state = tmp_path / "state.json"
    assert orch.round_once(runner.cfg, data, [].append, runner, dry=False, state_file=state)
    host.failed_sessions.add("s1")
    clock.now += 60
    assert orch.round_once(runner.cfg, data, [].append, runner, dry=False, state_file=state)
    runner.halt.check = lambda: [(orch.OK, "preflight")]
    clock.now += 60 * runner.cfg.halt_check_minutes
    assert orch.round_once(runner.cfg, data, [].append, runner, dry=False, state_file=state)
    claimed = orch.Reads(lambda: [issue("ABC-7", state="In Progress", area="docs")], list)
    assert orch.round_once(runner.cfg, claimed, [].append, runner, dry=False, state_file=state)
    assert runner.halt.cause is None and notices[-1] is None and len(notices) == 2


@pytest.mark.parametrize("after", ["gone", "todo"])
def test_an_agent_error_wait_ends_when_its_claim_wait_passes(tmp_path: Path, after: str) -> None:
    """The claim wait ends at its deadline even when the halted issue left the team's list, so the notice
    does not stay open for ever."""
    runner, host, writes, clock = runner_of(tmp_path, session_host="paseo", run_command="paseo run --no-wait --quiet x")
    notices = []
    runner.writes = dataclasses.replace(writes.writes(), notice=lambda cause, at: notices.append(cause))
    todo = orch.Reads(lambda: [issue("ABC-7", area="docs")], list)
    state = tmp_path / "state.json"
    assert orch.round_once(runner.cfg, todo, [].append, runner, dry=False, state_file=state)
    host.failed_sessions.add("s1")
    clock.now += 60
    assert orch.round_once(runner.cfg, todo, [].append, runner, dry=False, state_file=state)
    cause = "paseo start failed for ABC-7 (claude-sonnet-5-5): the agent stopped in error before its claim"
    runner.halt.check = lambda: [(orch.OK, "preflight")]
    clock.now += 60 * runner.cfg.halt_check_minutes
    assert orch.round_once(runner.cfg, todo, [].append, runner, dry=False, state_file=state)
    assert notices == [cause] and runner.halt.cause is None
    later = todo if after == "todo" else orch.Reads(list, list)
    clock.now += runner.cfg.claim_wait_seconds
    assert orch.round_once(runner.cfg, later, [].append, runner, dry=False, state_file=state)
    assert notices == [cause, None], "the notice closes when the claim wait passes"
    assert len(host.starts) == 2, "the restarted agent still runs, so nothing starts again"


def test_a_start_waits_for_the_setup_preflight_and_a_failure_halts_it(tmp_path: Path) -> None:
    """before a start the preflight reads the daemon's profile, and a failure stops the starts with the fix."""
    runner, host, _, _ = runner_of(tmp_path)
    problem = "Paseo daemon profile: pi-chatgpt, expected pi-lithosai: as agents, run `paseo daemon stop`"
    runner.prestart = lambda: [(orch.FAIL, problem)]
    assert runner.start_round([issue("ABC-7", area="docs")], [], dry=False) == [
        "no start this round: the setup preflight failed"]
    assert not host.starts and runner.halt.cause == problem


def test_the_setup_preflight_runs_only_before_a_real_start(tmp_path: Path) -> None:
    """a round whose slots are full starts nothing, so it calls no preflight (it lists pi's models for each cell);
    a round with a start calls it once, whatever the number of starts."""
    runner, host, _, _ = runner_of(tmp_path)
    calls: list[int] = []

    def prestart() -> list[orch.Line]:
        calls.append(1)
        return [(orch.OK, "preflight")]

    runner.prestart = prestart
    issues = [issue("ABC-5", state="In Progress", area="backend"), issue("ABC-6", state="In Progress", area="frontend"),
              issue("ABC-635", area="docs")]
    assert runner.start_round(issues, [], dry=False) == ["ABC-635 waits: no free slot"]
    assert calls == [] and host.starts == [], "full slots start nothing, so no preflight"

    # one slot free: the preflight runs once, before the start
    assert runner.start_round(issues[1:], [], dry=False) == ["started ABC-635 (claude-sonnet-5-5, medium)"]
    assert calls == [1] and len(host.starts) == 1

    # two starts in one round: one preflight for both
    runner2, host2, _, _ = runner_of(tmp_path)
    calls2: list[int] = []

    def prestart2() -> list[orch.Line]:
        calls2.append(1)
        return [(orch.OK, "preflight")]

    runner2.prestart = prestart2
    todo = [issue("ABC-8", area="backend"), issue("ABC-9", area="docs")]
    assert runner2.start_round(todo, [], dry=False) == ["started ABC-8 (claude-sonnet-5-5, medium)",
                                                        "started ABC-9 (claude-sonnet-5-5, medium)"]
    assert calls2 == [1] and len(host2.starts) == 2


def test_a_fix_start_waits_for_the_setup_preflight_too(tmp_path: Path) -> None:
    """a fix session is a start, so the setup preflight stops it as it stops a start from Todo."""
    runner, host, _, _ = runner_of(tmp_path)
    problem = "Paseo daemon profile: pi-chatgpt, expected pi-lithosai: as agents, run `paseo daemon stop`"
    runner.prestart = lambda: [(orch.FAIL, problem)]
    events = runner.start_fix(waiting_issue(), pr(7, "abc-7-x"), "changes requested", dry=False)
    assert [event.text for event in events] == ["no fix start this round: the setup preflight failed"]
    assert not host.starts and runner.halt.cause == problem
    assert runner.halt.failed_fix == "ABC-7", "the fix retries in the resume round, as a start that raised does"


@pytest.mark.parametrize("case", ["claimed", "late", "unreadable map", "archive failed"])
def test_a_setup_failure_needs_an_unclaimed_recent_start_and_a_readable_list(tmp_path: Path, case: str) -> None:
    """only an unclaimed start younger than the claim wait, on a readable list, is a setup failure. A key that
    claimed or is older is the run's own error, and an archive that fails leaves the halt and its notice as they are."""
    runner, host, _, clock = runner_of(tmp_path, session_host="paseo")
    host.failed_sessions.add("s1")
    runner.live["ABC-7"] = orch.Started("s1", clock.now)
    runner.tries["ABC-7"] = 1
    issues = [issue("ABC-7", area="docs")]
    if case == "claimed":
        issues = [issue("ABC-7", state="In Progress", area="docs")]
    elif case == "late":
        clock.now += runner.cfg.claim_wait_seconds
    elif case == "unreadable map":
        def boom(session: str) -> bool:
            raise RuntimeError("no agent list")
        host.failed = boom  # type: ignore[method-assign]
    else:
        host.refuse.add("s1")  # its archive fails; the halt and the notice matter more
    events = runner.setup_failure_events(issues)
    if case == "archive failed":
        assert [event.text for event in events] == ["start failed for ABC-7: the agent is in error before its claim"]
        assert runner.halt.cause and not runner.tries and not runner.live and host.closed == []
    else:
        assert events == [] and not runner.halt.cause and runner.tries == {"ABC-7": 1} and "ABC-7" in runner.live


def test_a_late_launch_holds_its_slot_when_the_teams_plan_read_fails(tmp_path: Path) -> None:
    runner, host, _, clock = runner_of(tmp_path, max_runs=1)
    cfg2 = dataclasses.replace(runner.cfg, team="XYZ")
    runner2 = orch.Runner(cfg2, host, clock=clock, halt=runner.halt)
    runner2.confirm_launch = lambda: [orch.Event("started", "XYZ-1", "started XYZ-1")]

    def unavailable() -> list[dict[str, Any]]:
        raise OSError("no issue list")

    teams = [orch.Team(runner.cfg, orch.Reads(lambda: [issue("ABC-7", area="docs")], list), runner),
             orch.Team(cfg2, orch.Reads(unavailable, list), runner2)]
    runner.elsewhere = orch.slots_elsewhere(teams, teams[0])
    runner.halt.enter(runner.cfg, clock.now, "launch unconfirmed")
    assert not orch.round_all(teams, lambda _: None, dry=False)
    assert not runner.halt.cause and runner.elsewhere() == 1 and not host.starts


def test_a_restart_does_not_start_an_issue_whose_session_still_works(tmp_path: Path) -> None:
    host = FakeHost()
    host.spaces = [{"workspace_id": "w1", "label": "ABC-7 ·", "agent_status": "working"}]
    restarted = orch.Runner(fix_cfg(tmp_path), host, None, Clock())  # no table of starts
    decided = restarted.start_decide([issue("ABC-7", area="backend")], [], dry=False)
    assert decided.items == [] and host.starts == [] and decided.running == ["ABC-7"]


def test_a_restarted_runner_counts_a_busy_session_of_a_todo_issue_as_a_slot(tmp_path: Path) -> None:
    host = FakeHost()
    host.spaces = [{"workspace_id": "w1", "label": "ABC-7 ·", "agent_status": "working"},
                   {"workspace_id": "w2", "label": "ABC-8 ·", "agent_status": "working"}]
    restarted = orch.Runner(fix_cfg(tmp_path, max_runs=2), host, None, Clock())
    issues = [issue("ABC-7", area="backend"), issue("ABC-8", area="frontend"), issue("ABC-9", area="docs")]
    decided = restarted.start_decide(issues, [], dry=False)
    assert host.starts == [] and decided.busy == {"ABC-7", "ABC-8"}
    assert restarted.held(issues, [], decided.busy) == {"ABC-7": "session busy, not claimed yet",
                                                         "ABC-8": "session busy, not claimed yet"}


def test_a_restarted_runner_holds_the_in_progress_issues_that_a_run_has(tmp_path: Path) -> None:
    """An In Progress issue with a live session and no pull request, and one with a draft pull request whose latest
    review asks for changes, are held from the first round after a restart; nothing starts past `max_runs`."""
    host = FakeHost()
    host.spaces = [{"workspace_id": "w1", "label": "ABC-7 ·", "agent_status": "working"},
                   {"workspace_id": "w2", "label": "ABC-8 ·", "agent_status": "working"}]
    restarted = orch.Runner(fix_cfg(tmp_path, max_runs=2), host, None, Clock())  # no table of starts
    issues = [issue("ABC-7", state="In Progress", area="backend"), issue("ABC-8", state="In Progress", area="frontend"),
              issue("ABC-9", area="docs")]
    prs = [pr(8, "abc-8-x", draft=True, reviews=ASKED)]
    decided = restarted.start_decide(issues, prs, dry=False)
    assert host.starts == [] and decided.items == [orch.Wait("ABC-9", orch.NO_SLOT)]
    assert restarted.held(issues, prs, decided.busy) == {"ABC-7": "In Progress", "ABC-8": "In Progress"}


def test_a_restarted_runner_holds_a_fix_that_no_draft_mark_claims_yet_busy_or_at_rest(tmp_path: Path) -> None:
    """The pull request is ready with changes requested: a busy session that works on the fix holds the slot, though this
    process did not start it, and so does an idle one (the issue is In Progress)."""
    host = FakeHost()
    host.spaces = [{"workspace_id": "w1", "label": "ABC-7 ·", "agent_status": "working"}]
    restarted = orch.Runner(fix_cfg(tmp_path, max_runs=1), host, None, Clock())
    issues = [issue("ABC-7", state="In Progress", area="backend"), issue("ABC-9", area="docs")]
    prs = [pr(7, "abc-7-x", reviews=ASKED)]
    decided = restarted.start_decide(issues, prs, dry=False)
    assert host.starts == [] and restarted.held(issues, prs, decided.busy) == {"ABC-7": "In Progress"}
    idle = FakeHost()  # the same issue with an idle session: it holds the slot too
    idle.spaces = [{"workspace_id": "w1", "label": "ABC-7 ·", "agent_status": "idle"}]
    other = orch.Runner(fix_cfg(tmp_path, max_runs=1), idle, None, Clock())
    assert other.held(issues, prs, other.start_decide(issues, prs, dry=False).busy) == {"ABC-7": "In Progress"}
    assert idle.starts == []


def test_a_session_that_a_restarted_round_wakes_holds_its_slot_in_that_round(tmp_path: Path) -> None:
    """The log of 2026-11-24 13:03:12 UTC: after a restart `live` was empty, two idle sessions of In Progress issues
    with ready pull requests that ask for changes held no slot, and the round started three issues beside them (5 of 3)."""
    host = FakeHost()
    host.wakeable = True
    host.spaces = [{"workspace_id": "w1", "label": "ABC-7 ·", "agent_status": "idle"},
                   {"workspace_id": "w2", "label": "ABC-8 ·", "agent_status": "idle"}]
    clock = Clock()
    restarted = orch.Runner(fix_cfg(tmp_path, max_runs=3), host, None, clock)
    clock.now = T0 + 60
    issues = [issue("ABC-7", state="In Progress", area="backend"), issue("ABC-8", state="In Progress", area="frontend"),
              issue("ABC-9", area="docs"), issue("ABC-10", area="infra"), issue("ABC-11", area="web")]
    prs = [pr(7, "abc-7-x", reviews=ASKED), pr(8, "abc-8-x", reviews=ASKED)]
    plan, events = restarted.plan(issues, prs, dry=False)
    assert [e.key for e in events if e.kind == "woke"] == ["ABC-7", "ABC-8"]
    assert len(host.starts) == 1  # 3 slots: two woken runs and one start
    assert {s["key"] for s in plan["slots"]} == {"ABC-7", "ABC-8", "ABC-9"} and plan["building"] == 3
    clock.now += restarted.cfg.claim_grace_seconds  # past the grace time: the issues are In Progress, so they hold their slots still
    assert set(restarted.held(issues, prs, set())) == {"ABC-7", "ABC-8"}


def test_a_session_that_does_not_wake_holds_its_slot_too(tmp_path: Path) -> None:
    issues, prs = [issue("ABC-7", state="In Progress", area="backend")], [pr(7, "abc-7-x", reviews=ASKED)]
    runner, host, _, clock = runner_of(tmp_path)
    idle_host(host)
    clock.now = T0 + 60
    assert runner.plan(issues, prs, dry=True)[0]["slots"] == [{"key": "ABC-7", "reason": "In Progress"}]  # a dry run wakes nobody
    runner, host, _, clock = runner_of(tmp_path)  # a fresh runner: the dry run above kept `woke` for the event
    idle_host(host)
    clock.now = T0 + 60
    assert runner.fix_round(issues, prs, dry=False) == [] and host.wakes == [("ABC-7", WAKE_TEXT)]  # sent; the host answered False
    assert runner.held(issues, prs, set()) == {"ABC-7": "In Progress"}


def test_a_fix_round_that_no_session_shows_holds_a_slot_and_a_review_in_progress_holds_none(tmp_path: Path) -> None:
    """The log of 2026-11-24 16:57 UTC: a fix round whose pull request says "changes requested" and whose session is
    not busy and not in `live` freed its slot for a moment, and the round started a new issue (`build 5/3`)."""
    def decide(fix_state: str) -> tuple[orch.Runner, FakeHost, orch.StartRound, list[dict[str, Any]], list[dict[str, Any]]]:
        host = FakeHost()
        host.spaces = [{"workspace_id": "w1", "label": "ABC-8 ·", "agent_status": "working"},
                       {"workspace_id": "w3", "label": "ABC-7 ·", "agent_status": "idle"}]
        runner = orch.Runner(fix_cfg(tmp_path, max_runs=3), host, None, Clock())
        issues = [issue("ABC-6", state="In Progress", area="backend"), issue("ABC-8", state="In Progress", area="frontend"),
                  issue("ABC-7", state=fix_state, area="infra"), issue("ABC-9", area="docs")]
        prs = [pr(7, "abc-7-x", reviews=ASKED)]
        return runner, host, runner.start_decide(issues, prs, dry=False), issues, prs

    runner, host, decided, issues, prs = decide("In Progress")
    assert host.starts == [] and decided.items == [orch.Wait("ABC-9", orch.NO_SLOT)]
    assert runner.held(issues, prs, decided.busy) == {"ABC-6": "In Progress", "ABC-7": "In Progress", "ABC-8": "In Progress"}
    runner, host, decided, issues, prs = decide("In Review")  # the same pull request in review: its issue is not In Progress
    assert [s[1] for s in host.starts] == ["run-it ABC-9 start"]
    assert set(runner.held(issues, prs, decided.busy)) == {"ABC-6", "ABC-8", "ABC-9"}


def test_a_session_that_is_not_busy_does_not_hold_its_issue(tmp_path: Path) -> None:
    for status in ("done", "unknown"):  # an idle one gets a wake instead (`wake_todo`)
        host = FakeHost()
        host.spaces = [{"workspace_id": "w1", "label": "ABC-7 ·", "agent_status": status},
                       {"workspace_id": "w2", "label": "ABC-8 ·", "agent_status": "working"},
                       {"workspace_id": "w3", "label": "abc-7-by-hand", "agent_status": "working"}]  # not the orchestrator's label
        restarted = orch.Runner(fix_cfg(tmp_path), host, None, Clock())
        assert restarted.start_round([issue("ABC-7", area="backend")], [], dry=False) == ["started ABC-7 (claude-sonnet-5-5, medium)"]


def test_a_failed_session_list_starts_nothing_and_gives_one_event(tmp_path: Path) -> None:
    class NoList(FakeHost):
        def workspaces(self) -> list[dict[str, Any]]:
            raise RuntimeError("herdr workspace list failed")

    host = NoList()
    runner = orch.Runner(fix_cfg(tmp_path), host, None, Clock())
    decided = runner.start_decide([issue("ABC-7", area="backend"), issue("ABC-8", area="frontend")], [], dry=False)
    assert [(e.kind, e.key, e.reason) for e in decided.items] == [("start_failed", "", "RuntimeError")]
    assert decided.items[0].text == "no start this round: the session list failed: RuntimeError" and host.starts == []


def test_start_round_reads_the_session_list_once(tmp_path: Path) -> None:
    class Counting(FakeHost):
        reads = 0

        def workspaces(self) -> list[dict[str, Any]]:
            Counting.reads += 1
            return super().workspaces()

    runner = orch.Runner(fix_cfg(tmp_path), Counting(), None, Clock())
    runner.start_round([issue("ABC-7", area="backend"), issue("ABC-8", area="frontend")], [], dry=False)
    assert Counting.reads == 1


def test_start_round_reads_the_area_of_a_closed_origin_once(tmp_path: Path) -> None:
    runner, _, _, _ = runner_of(tmp_path)
    asked: list[str] = []

    def labels(key: str) -> list[str]:
        asked.append(key)
        return ["follow-up", "infra"]

    issues = [issue("ABC-50", description="Found in ABC-49"), issue("ABC-51", area="infra")]
    assert runner.start_round(issues, [], dry=True, labels=labels) == ["would start ABC-50 (claude-sonnet-5-5, medium)", "ABC-51 waits: area infra: ABC-50 runs"]
    assert asked == ["ABC-49"]


def test_the_example_config_lists_the_areas_and_turns_the_area_rule_off() -> None:
    assert EXAMPLE.distinct_areas is False and "docs" in EXAMPLE.areas and "orchestrator" in EXAMPLE.areas
    assert CFG.review_states == ("In Review", "Approved")  # Escalated holds no Area


def test_config_takes_no_areas_when_the_area_rule_is_off(tmp_path: Path) -> None:
    loaded = orch.load_config(config_with(tmp_path, distinct_areas="distinct_areas = false", areas="areas = []"))
    assert loaded.distinct_areas is False and loaded.areas == ()


def test_start_round_never_starts_an_l_a_hands_off_a_workflow_or_a_blocked_issue(tmp_path: Path) -> None:
    runner, host, _, _ = runner_of(tmp_path, max_runs=9)
    issues = [issue("ABC-71", size="L", area="backend"), issue("ABC-72", labels=("hands-off",), area="infra"),
              issue("ABC-73", area="workflow"), issue("ABC-74", blocked=(("ABC-1", "Todo"),), area="frontend"),
              issue("ABC-75", area="docs")]
    assert runner.start_round(issues, [], dry=False) == ["started ABC-75 (claude-sonnet-5-5, medium)"]
    assert [c for _, c, _ in host.starts] == ["run-it ABC-75 start"]


# -- the origin of an old follow-up, a failed origin read, a start that never claims, skip_labels' default ------


def test_an_old_follow_up_from_a_pull_request_takes_the_area_of_the_pull_requests_issue(tmp_path: Path) -> None:
    runner, _, _, _ = runner_of(tmp_path)
    asked: list[Any] = []

    def labels(key: str) -> list[str]:
        asked.append(key)
        return ["infra"] if key == "ABC-12" else []

    def branch(number: int) -> str:
        asked.append(number)
        return "abc-12-short-name"

    old = issue("ABC-60", description="From: PR #50, round 2, abc, depth 1")
    assert runner.start_round([old, issue("ABC-61", area="infra")], [], dry=True, labels=labels, branch=branch) == [
        "would start ABC-60 (claude-sonnet-5-5, medium)", "ABC-61 waits: area infra: ABC-60 runs"]
    assert asked == [50, "ABC-12"]
    # an open pull request is in the read already: no read of its branch (its follow-up waits for the merge)
    asked.clear()
    still_open = issue("ABC-62", description="From: PR #7, round 1, abc, depth 1")
    assert runner.start_round([still_open], [pr(7, "abc-12-x")], dry=True, labels=labels, branch=branch) == ["ABC-62 waits: after the merge of PR #7"]
    assert asked == ["ABC-12"]


def test_a_follow_up_starts_only_after_its_pull_request_merged(tmp_path: Path) -> None:
    # the reviewer files the follow-up with the approve; main has the change only after the merge
    runner, host, _, _ = runner_of(tmp_path, max_runs=9)
    follow_up = issue("ABC-832", area="frontend", description="From: PR #57, round 2, 519ea7e, depth 1\n\n- [ ] R1-3 a line")
    others = [issue("ABC-60", area="docs", description="Found in ABC-796"),  # work found on the way does not wait
              issue("ABC-61", area="backend", description="From: PR #75, round 1, abc, depth 1")]  # another number: #75 is not open
    open_pr = [pr(57, "abc-796-compact-header")]
    assert runner.start_round([follow_up, *others], open_pr, dry=False) == [
        "ABC-832 waits: after the merge of PR #57", "started ABC-60 (claude-sonnet-5-5, medium)", "started ABC-61 (claude-sonnet-5-5, medium)"]
    assert [c for _, c, _ in host.starts] == ["run-it ABC-60 start", "run-it ABC-61 start"]
    plan, _ = runner.plan([follow_up], open_pr, dry=True)
    assert plan["other_waits"] == [{"key": "ABC-832", "reason": "after the merge of PR #57"}] and plan["areas"] == []
    # the pull request merged (or closed): it is no longer among the open ones, and the follow-up starts
    assert runner.start_round([follow_up], [pr(758, "abc-800-other")], dry=False) == ["started ABC-832 (claude-sonnet-5-5, medium)"]
    assert orch.unmerged_origin(follow_up, open_pr) == 57 and orch.unmerged_origin(follow_up, []) is None
    assert orch.unmerged_origin(issue("ABC-1", description="Found in ABC-757"), open_pr) is None
    assert orch.unmerged_origin(issue("ABC-1"), open_pr) is None


def test_a_collecting_follow_up_waits_for_each_pull_request_it_came_from() -> None:
    """one follow-up issue per Area collects the findings of several reviews, one `From: PR #N` line each at its
    top. It starts only when none of those pull requests is open; `origin_key` still reads the first line."""
    two = issue("ABC-1", description="From: PR #57, round 2, abc1234, depth 1\nFrom: PR #60, round 1, def5678, depth 2\n"
                                     "Take it when: x\n\n## From PR #57\n\n- [ ] R1-1 a.py:1 one")
    assert orch.origin_prs(two) == [57, 60]
    assert orch.unmerged_origin(two, [{"number": 60}]) == 60, "the second origin is still open"
    assert orch.unmerged_origin(two, [{"number": 57}, {"number": 60}]) == 57
    assert orch.unmerged_origin(two, [{"number": 1}]) is None, "both merged: it starts"
    assert orch.origin_key(two) == "#57", "the first origin is the issue's origin"
    assert orch.origin_prs(issue("ABC-2", description="Found in ABC-57\nFrom: PR #9")) == [], "only lines at the top"


def test_a_failed_origin_read_holds_only_its_issue(tmp_path: Path) -> None:
    runner, _, _, _ = runner_of(tmp_path)

    def labels(key: str) -> list[str]:
        raise RuntimeError(f"no issue {key}")

    issues = [issue("ABC-70", description="Found in ABC-1"), issue("ABC-71", area="docs")]
    assert runner.start_round(issues, [], dry=True, labels=labels) == [
        "ABC-70 waits: no Area label, and its origin could not be read (RuntimeError: no issue ABC-1)", "would start ABC-71 (claude-sonnet-5-5, medium)"]


def test_a_start_that_never_claims_is_tried_once_more_then_waits(tmp_path: Path) -> None:
    runner, host, _, clock = runner_of(tmp_path)
    todo = [issue("ABC-635", area="docs")]
    assert runner.start_round(todo, [], dry=False) == ["started ABC-635 (claude-sonnet-5-5, medium)"]
    host.ended.add("s1")  # the session ended before its claim: Linear still says Todo
    clock.now += 121
    assert runner.start_round(todo, [], dry=False) == ["started ABC-635 (claude-sonnet-5-5, medium)"]
    host.ended.add("s2")
    clock.now += 121
    waits = ["ABC-635 waits: started twice and never claimed: read its last session; to start it again, run "
             "`owlcode restart` or move it out of Todo and back"]
    assert runner.start_round(todo, [], dry=False) == waits
    clock.now += 3600
    assert runner.start_round(todo, [], dry=False) == waits
    assert len(host.starts) == 2
    # once claimed (In Progress), the count goes; back in Todo later, it starts again
    runner.start_round([issue("ABC-635", state="In Progress", area="docs")], [], dry=False)
    assert runner.start_round(todo, [], dry=False) == ["started ABC-635 (claude-sonnet-5-5, medium)"]


def test_a_run_that_claimed_and_gave_the_issue_back_is_no_start_never_claimed(tmp_path: Path) -> None:
    runner, host, _, clock = runner_of(tmp_path)

    def stamp() -> str:
        return datetime.fromtimestamp(clock.now + 5, tz=UTC).isoformat()

    def given_back(n: int) -> dict[str, Any]:
        # the orchestrator's run line, the run's own run line and the give-back, all made after the start
        made = issue("ABC-832", area="docs")
        made["comments"] = [{"created": stamp(), "body": b} for b in ("Run: m, effort e", "Run: m, effort e", f"Back {n}")]
        return made

    for n in (1, 2, 3):
        assert runner.start_round([issue("ABC-832", area="docs")], [], dry=False) == ["started ABC-832 (claude-sonnet-5-5, medium)"]
        host.ended.add(f"s{n}")
        clock.now += 61
        # a round reads Todo again: the run claimed and gave it back in between
        assert runner.start_round([given_back(n)], [], dry=False) == []
        clock.now += 121
    assert len(host.starts) == 3


def test_only_a_comment_after_the_start_beyond_the_run_line_shows_a_claim(tmp_path: Path) -> None:
    runner, _, _, clock = runner_of(tmp_path)
    started = clock.now
    runner.last_start["ABC-9"] = started

    def with_comments(*stamps: float) -> dict[str, Any]:
        made = issue("ABC-9")
        made["comments"] = [{"created": datetime.fromtimestamp(t, tz=UTC).isoformat(), "body": "x"} for t in stamps]
        return made

    assert not runner.claimed_since_start(with_comments())  # no comment
    assert not runner.claimed_since_start(with_comments(started + 1))  # the orchestrator's own run line only
    assert not runner.claimed_since_start(with_comments(started - 9, started - 8, started + 1))  # older comments do not count
    assert runner.claimed_since_start(with_comments(started + 1, started + 2))
    assert not runner.claimed_since_start(issue("ABC-10"))  # no start in this table (a restart lost it)
    bare = orch.Runner(runner.cfg, runner.host, None, clock)  # a dry run writes no run line: one comment is a sign
    bare.last_start["ABC-9"] = started
    assert bare.claimed_since_start(with_comments(started + 1))


def test_a_config_names_its_skip_labels_and_never_starts_a_workflow_issue(tmp_path: Path) -> None:
    loaded = orch.load_config(config_with(tmp_path))
    assert loaded.skip_labels == ("hands-off", "workflow")
    assert not orch.can_start(issue(area="workflow"), loaded)
    assert orch.can_start(issue(area="docs"), loaded)


def test_the_example_skips_only_hands_off_and_starts_a_workflow_issue_like_any_other(tmp_path: Path) -> None:
    """A `workflow` issue starts and resumes like any other; `hands-off` never does."""
    assert EXAMPLE.skip_labels == ("hands-off",)
    assert orch.can_start(issue(area="workflow"), EXAMPLE) and not orch.can_start(issue(labels=("hands-off",)), EXAMPLE)
    asked = pr(reviews=(("CHANGES_REQUESTED", "2026-11-02T10:00:00Z"),))
    assert orch.can_resume(issue(state="In Progress", area="workflow"), asked, EXAMPLE) == "changes requested"
    assert orch.can_resume(issue(state="In Progress", labels=("hands-off",)), asked, EXAMPLE) is None
    runner, host, _, _ = runner_of(tmp_path, **{k: getattr(EXAMPLE, k) for k in ("max_runs", "distinct_areas", "skip_labels")})
    issues = [issue("ABC-72", labels=("hands-off",), area="infra"), issue("ABC-73", area="workflow"),
              issue("ABC-74", area="workflow"), issue("ABC-75", area="workflow")]
    assert runner.start_round(issues, [], dry=False) == [  # one Area, three runs: no Area rule, three slots
        "started ABC-73 (claude-sonnet-5-5, medium)", "started ABC-74 (claude-sonnet-5-5, medium)",
        "started ABC-75 (claude-sonnet-5-5, medium)"]
    assert [c for _, c, _ in host.starts] == ["run-it ABC-73 start", "run-it ABC-74 start", "run-it ABC-75 start"]


def test_the_config_s_defaults_are_the_example_s_values() -> None:
    """`Config`'s defaults follow the example for the slots and the Area rule; the skip labels name no product."""
    default = orch.Config()
    assert (default.max_runs, default.distinct_areas, default.skip_labels) == (EXAMPLE.max_runs, EXAMPLE.distinct_areas, ())


def test_recovery_does_not_drop_a_notice_that_linear_could_not_write() -> None:
    halt = orch.Halt(check=lambda: [(orch.OK, "preflight")])
    halt.enter(CFG, T0, "host connection failed")

    def unavailable(cause: str | None, at: float) -> None:
        raise OSError("Linear unavailable")

    with pytest.raises(OSError):
        halt.notify(unavailable, T0)
    now = T0 + 60 * CFG.halt_check_minutes
    assert halt.recover(CFG, now)
    writes = []
    halt.notify(lambda cause, at: writes.append((cause, at)), now)
    assert writes == [("host connection failed", T0), (None, now)]


def test_a_lost_notice_close_reply_does_not_hide_the_next_halt() -> None:
    halt = orch.Halt(check=lambda: [(orch.OK, "preflight")])
    notices = []
    halt.enter(CFG, T0, "start failed")

    def write(cause: str | None, at: float) -> None:
        notices.append(cause)
        if cause is None:
            raise OSError("lost close reply")

    halt.notify(write, T0)
    now = T0 + 60 * CFG.halt_check_minutes
    assert halt.recover(CFG, now)
    with pytest.raises(OSError):
        halt.notify(write, now)
    halt.enter(CFG, now + 1, "start failed")
    halt.notify(write, now + 1)
    assert notices == ["start failed", None, "start failed"]


def test_the_setup_notice_reuses_the_open_issue_and_closes_it_on_recovery() -> None:
    from unittest.mock import Mock

    api = Mock()
    api.issues.return_value = []
    orch.halt_notice(EXAMPLE, api, "model not found", T0)
    args, kwargs = api.create.call_args
    assert args[:2] == ("ABC", "Orchestrator halted: model not found")
    assert args[3:] == (["hands-off", "orchestrator"], "Escalated", "high") and kwargs == {"assignee": "me"}
    api.issues.return_value = [{"identifier": "ABC-9", "state": "Triage", "description": args[2], "comments": []}]
    orch.halt_notice(EXAMPLE, api, "model not found", T0 + 300)
    api.comment.assert_not_called()
    orch.halt_notice(EXAMPLE, api, "credential failure", T0 + 1)
    assert api.comment.call_args.args[0] == "ABC-9" and api.create.call_count == 1
    api.issues.return_value[0]["comments"] = [{"body": api.comment.call_args.args[1]}]
    orch.halt_notice(EXAMPLE, api, "credential failure", T0 + 301)
    api.comment.assert_called_once()
    api.team.return_value = {"states": [{"name": "Canceled", "type": "canceled"}, {"name": "Done", "type": "completed"}]}
    orch.halt_notice(EXAMPLE, api, None, T0 + 600)
    body = api.comment.call_args.args[1]
    assert "the setup halt cleared" in body
    api.move.assert_called_once_with("ABC-9", "Done")
    api.issues.assert_called_with("ABC", [])
    # The comment landed, but the issue still needs to move (for example, after a failed move and a restart).
    api.issues.return_value[0]["comments"] = [{"body": body}]
    api.comment.reset_mock()
    orch.halt_notice(EXAMPLE, api, None, T0 + 660)
    api.comment.assert_not_called()

    # If the issue is still open, an old cause after the resume needs a new comment.
    orch.halt_notice(EXAMPLE, api, "model not found", T0 + 720)
    api.comment.assert_called_once()
    new_halt = api.comment.call_args.args[1]
    assert "model not found" in new_halt
    api.issues.return_value[0]["comments"].append({"body": new_halt})
    orch.halt_notice(EXAMPLE, api, None, T0 + 780)
    assert api.comment.call_count == 2
    assert api.comment.call_args.args[1].startswith("resumed at ")


# -- --check: the pure check with a fake log and fake replies ---------------------------------------------------

NOW = datetime.fromisoformat("2026-11-10T12:00:00+00:00").timestamp()
DAY = 86400
# The log prints only changes: no round line in the last three poll periods; the state file holds the round's time.
FRESH_LOG = ("2026-11-10T11:00:00Z waiting: the agent's keychain is locked (the check exited 51); next try in 60 s\n"
             "2026-11-10T11:01:00Z starting the orchestrator\n2026-11-10T11:02:00Z build 0/2 · review 0 · you 0\nnothing to do\n")


def state_at(stamp: str | None, failed: str | None = None) -> str:
    return orch.json.dumps({"version": 1, "time": stamp, **({"failed": {"time": failed, "error": "OSError"}} if failed else {})})


FRESH_STATE = state_at("2026-11-10T11:59:00Z")
INSTALLED = "c" * 40


def probes(environ: dict[str, str] | None = None, log: str | None = FRESH_LOG, sessions: list[dict[str, Any]] | None = None,
           missing: tuple[str, ...] = (), linear_down: bool = False, commit: str | None = INSTALLED, state: str | None = FRESH_STATE,
           started: Callable[[str], float | None] = lambda key: None) -> Any:
    def linear() -> None:
        if linear_down:
            raise OSError("secret reply text")

    return orch.Probes(environ={"LINEAR_API_KEY": "lin_api_secret"} if environ is None else environ, linear=linear,
                       github=lambda: None, log=lambda: log, sessions=lambda: sessions or [],
                       installed=lambda: (commit, list(missing)), state=lambda: state, run_started=started, clock=lambda: NOW)


def check(p: Any) -> tuple[int, list[str]]:
    said: list[str] = []
    return orch.run_check(CFG, p, said.append), said


def test_check_all_ok() -> None:
    code, said = check(probes())
    assert code == 0 and all(line.startswith("ok ") for line in said), said
    assert "ok   the service: last round at 2026-11-10T11:59:00Z" in said
    assert not any("lin_api_secret" in line for line in said)
    # a person who expects the local review on another model sees the one that runs, and where to change it
    assert ("ok   local review: claude-sonnet-5-5, effort high (the claude profile): "
            "`owlcode restart` writes a change of it beside the installed agent_user.py") in said


def test_a_profile_needs_its_local_review_s_model_and_effort(tmp_path: Path) -> None:
    """the config names the local review's model and effort level of each profile, and no default sits in
    the code: a table without them is refused, with the example's own line pasted into the refusal."""
    for key in ("review_model", "review_effort"):
        with pytest.raises(orch.ConfigError) as missing:
            orch.load_config(config_with(tmp_path, **{key: None}))
        assert "missing in" in str(missing.value) and f"[profiles.claude] {key} = " in str(missing.value)
    for bad in ('review_model = "a model"', "review_effort = 3", 'review_effort = ""'):
        with pytest.raises(orch.ConfigError, match=r"must be a model's or an effort level's name"):
            orch.load_config(config_with(tmp_path, **{bad.split(" =")[0]: bad}))
    cfg = orch.load_config(config_with(tmp_path))
    assert cfg.review == ("claude-sonnet-5-5", "high") and cfg.inference_profile == "claude", "the profile in force's"
    assert (cfg.profile_key_check, cfg.inference_profile) == (EXAMPLE.profile_key_check, "claude")
    for name, table in tomllib.loads(EXAMPLE_TEXT)["profiles"].items():
        assert (table["review_model"], table["review_effort"]) == (
            ("claude-sonnet-5-5", "high") if name == "claude" else
            ("gpt-6.1-sol", "high") if name == "pi-chatgpt" else
            ("zai-org/GLM-5.3-Flash", "max") if name == "pi-lithosai" else ("deepseek-v4.1-flash", "high")), name
    assert orch.check_review(dataclasses.replace(cfg, review=("glm-5.3", ""))) == (
        orch.OK, "local review: glm-5.3, effort none (the claude profile): `owlcode restart` writes a change of "
                 "it beside the installed agent_user.py")


def test_check_a_stale_round_fails() -> None:
    code, said = check(probes(state=state_at("2026-11-10T11:56:59Z")))  # 3 min 1 s: more than three periods of 60 s
    assert code == 1 and "FAIL the service: no round since 2026-11-10T11:56:59Z" in said
    assert check(probes(state=state_at("2026-11-10T11:57:00Z")))[0] == 0
    assert check(probes(state=state_at("2026-11-10T11:00:00Z", failed="2026-11-10T11:59:00Z")))[0] == 0  # a failed round polled too


def test_check_reads_the_round_time_that_a_round_writes_to_the_state_file(tmp_path: Path) -> None:
    """a quiet log has no round line for hours; the state file has the time of each round, good or failed."""
    runner, _, _, clock = runner_of(tmp_path)
    state = tmp_path / "s.json"
    quiet_log = "2026-11-10T00:00:00Z starting the orchestrator\n"
    assert orch.round_once(runner.cfg, reads_of([issue("ABC-2", area="docs")], []), [].append, runner, dry=True, state_file=state)
    assert orch.check_service(quiet_log, state.read_text(), clock.now + 60, 60)[0] == orch.OK
    assert orch.check_service(quiet_log, state.read_text(), clock.now + 181, 60)[0] == orch.FAIL  # old
    clock.now += 600
    assert not orch.round_once(runner.cfg, reads_of(OSError("Linear is down"), []), [].append, runner, dry=True, state_file=state)
    assert orch.check_service(quiet_log, state.read_text(), clock.now + 60, 60)[0] == orch.OK  # a failed round counts too
    assert orch.json.loads(state.read_text())["failed"]["error"] == "OSError"


def test_check_a_newer_waiting_line_fails_with_the_keychain() -> None:
    log = FRESH_LOG + "2026-11-10T11:59:30Z waiting: the agent's keychain is locked (the check exited 51); next try in 60 s\n"
    code, said = check(probes(log=log))
    assert code == 1 and f"FAIL {orch.KEYCHAIN_LOCKED}" in said
    # the line names the unlock that reaches the service (a pane's does not, after a restart), and the older LaunchDaemon
    assert "locked in the service's session" in orch.KEYCHAIN_LOCKED and "owlcode unlock" in orch.KEYCHAIN_LOCKED
    assert "older LaunchDaemon" in orch.KEYCHAIN_LOCKED
    code, said = check(probes(log="2026-11-10T11:59:30Z waiting: locked\n", state=None))  # waiting since the start: no round yet
    assert code == 1 and any("keychain is locked" in line for line in said)


def test_check_no_log_or_no_round_fails() -> None:
    assert "FAIL the service: no log: is it installed?" in check(probes(log=None))[1]
    started = "2026-11-10T11:01:00Z starting the orchestrator\n"
    assert "FAIL the service: no state file: is it installed?" in check(probes(log=started, state=None))[1]
    assert "FAIL the service: the state file holds no round" in check(probes(log=started, state="{"))[1]
    assert "FAIL the service: the state file holds no round" in check(probes(log=started, state=state_at(None)))[1]


def test_check_a_missing_key_fails_and_never_prints_a_key() -> None:
    code, said = check(probes(environ={}))
    assert code == 1 and "FAIL keys: ABC: LINEAR_API_KEY is not set" in said


def test_check_an_unreachable_linear_prints_the_error_type_only() -> None:
    code, said = check(probes(linear_down=True))
    assert code == 1 and "FAIL Linear: no answer (OSError)" in said
    assert not any("secret reply text" in line for line in said)


def test_check_commits_not_installed_warn() -> None:
    code, said = check(probes(missing=("d" * 40, "e" * 40)))
    assert code == 0
    assert f"WARN installed: {INSTALLED[:12]} lacks 2 commits of main under its files: run owlcode install" in said
    assert any(line.startswith("WARN installed: no COMMIT") for line in check(probes(commit=None))[1])


def test_check_an_installed_commit_of_another_repository_warns_and_never_fails() -> None:
    code, said = check(dataclasses.replace(probes(), installed=lambda: (INSTALLED, None)))
    assert code == 0
    assert "WARN installed: the installed version is not a commit of this checkout: run owlcode install" in said


def test_check_a_session_that_works_over_a_day_warns_with_its_key() -> None:
    sessions = [{"label": "ABC-12 ·", "agent_status": "working"},   # a day and a second: warns
                {"label": "ABC-13 ·", "agent_status": "done"},      # finished: no warning
                {"label": "ABC-14 ·", "agent_status": "working"},   # under a day
                {"label": "agents", "agent_status": "working"}]  # not the orchestrator's
    since = {"ABC-12": NOW - DAY - 1, "ABC-13": NOW - 5 * DAY, "ABC-14": NOW - DAY + 1}
    code, said = check(probes(sessions=sessions, started=since.get))
    assert code == 0 and [line for line in said if line.startswith("WARN")] == ["WARN sessions: ABC-12 still works, 24 hours after its start"]
    assert "ok   sessions: none works for more than a day" in check(probes())[1]


def test_check_a_busy_session_whose_issue_has_no_run_line_warns_that_its_age_is_unknown() -> None:
    sessions = [{"label": "ABC-12 ·", "agent_status": "working"}]
    code, said = check(probes(sessions=sessions))
    assert code == 0 and "WARN sessions: ABC-12 works, and its issue has no run line: its age is unknown" in said

    def boom(key: str) -> float | None:
        raise OSError("secret reply text")

    code, said = check(probes(sessions=sessions, started=boom))
    assert code == 0 and "WARN sessions: ABC-12 works, and its run line could not be read (OSError)" in said
    assert not any("secret" in line for line in said)


def test_the_run_start_is_the_newest_run_line_of_the_issue() -> None:
    comments = [{"created": "2026-11-10T22:16:48.276Z", "body": "Run: claude-opus-5-5, effort unknown\n"},
                {"created": "2026-11-10T22:18:23.491Z", "body": "Question: where does the time go?"},
                {"created": "2026-11-15T00:59:23.187Z", "body": "Run: claude-sonnet-5-5, effort medium"},
                {"created": "2026-11-16T00:00:00.000Z", "body": "A note: Run: not at the start"}]
    assert orch.run_started(comments) == orch._seconds("2026-11-15T00:59:23.187Z")
    assert orch.run_started(comments[1:2] + comments[3:]) is None and orch.run_started([]) is None


def test_check_a_probe_that_raises_gives_a_line_not_a_crash() -> None:
    def boom() -> Any:
        raise RuntimeError("herdr workspace list failed")

    p = orch.Probes(environ={"LINEAR_API_KEY": "x"}, linear=lambda: None, github=lambda: None, log=boom, sessions=boom,
                    installed=boom, state=boom, run_started=lambda key: None, clock=lambda: NOW)
    code, said = check(p)
    assert code == 1
    assert "FAIL the service: cannot read the log or the state file (RuntimeError)" in said
    assert "WARN sessions: herdr gave no list (RuntimeError)" in said
    assert "WARN installed: cannot compare with main (RuntimeError)" in said


def test_check_reads_its_settings_from_the_service_table() -> None:
    assert CFG.log_file == "/Users/agents/Library/Logs/orchestrator.log"
    assert CFG.install_dir == "/usr/local/lib/orchestrator" and CFG.clone == "/Users/agents/owlcode"


def test_check_knows_the_files_that_the_install_copies() -> None:
    spec = importlib.util.spec_from_file_location("orchestrator_install_files", REAL_REPO / "orchestrator" / "deploy" / "install.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    assert set(orch.INSTALLED_FILES) == set(module.FILES)


def test_read_log_tail_and_installed_commits(tmp_path: Path) -> None:
    log = tmp_path / "orchestrator.log"
    assert orch.read_log_tail(log) is None
    log.write_text("x" * (orch.LOG_TAIL_BYTES + 10) + "\nround 2026-11-10T11:59:00Z\n")
    tail = orch.read_log_tail(log)
    assert tail is not None and len(tail) == orch.LOG_TAIL_BYTES and tail.endswith("round 2026-11-10T11:59:00Z\n")
    cfg = orch.Config(install_dir=str(tmp_path), clone="/clone")
    assert orch.read_installed(cfg, run=lambda argv: None) == (None, [])  # no COMMIT: not installed
    (tmp_path / "COMMIT").write_text(INSTALLED + "\n")
    calls: list[list[str]] = []

    def run(argv: Any) -> Any:
        calls.append(list(argv))
        return orch.subprocess.CompletedProcess(argv, 0, "d" * 40 + "\n" if "log" in argv else "", "")

    assert orch.read_installed(cfg, run=run) == (INSTALLED, ["d" * 40])
    assert calls[2] == ["git", "-C", "/clone", "cat-file", "-e", f"{INSTALLED}^{{commit}}"]
    assert calls[3][:6] == ["git", "-C", "/clone", "log", "--format=%H", f"{INSTALLED}..origin/main"]
    assert calls[3][7:] == list(orch.INSTALLED_FILES)


def test_read_installed_a_commit_of_another_repository_gives_no_list_and_no_git_log() -> None:
    cfg = orch.Config(install_dir=str(Path(tempfile.mkdtemp())), clone="/clone")
    (Path(cfg.install_dir) / "COMMIT").write_text(INSTALLED + "\n")
    calls: list[list[str]] = []

    def run(argv: Any) -> Any:
        calls.append(list(argv))
        return orch.subprocess.CompletedProcess(argv, 128 if "cat-file" in argv else 0, "", "fatal: Not a valid object name")

    assert orch.read_installed(cfg, run=run) == (INSTALLED, None)
    assert not any("log" in c for c in calls)
    # a clone that is no repository is no unknown commit: the check says it cannot compare
    with pytest.raises(RuntimeError):
        orch.read_installed(cfg, run=lambda argv: orch.subprocess.CompletedProcess(argv, 128, "", "fatal: not a git repository"))


def test_main_check_passes_the_exit_code(capsys: pytest.CaptureFixture[str]) -> None:
    assert orch.main(["--config", str(REAL_REPO / "orchestrator" / "orchestrator.example.toml"), "--check"], probes=probes(log=None)) == 1
    assert "FAIL the service: no log" in capsys.readouterr().out


def test_read_sessions_parses_herdrs_reply_and_refuses_a_failed_one() -> None:
    reply = ('{"id":"cli:workspace:list","result":{"type":"workspace_list","workspaces":['
             '{"workspace_id":"w1","label":"ABC-12 ·","agent_status":"working","number":1},'
             '{"workspace_id":"w2","label":"agents","agent_status":"idle","number":2}]}}')
    sessions = orch.read_sessions(run=lambda argv: orch.subprocess.CompletedProcess(argv, 0, reply, ""))
    assert [s["label"] for s in sessions] == ["ABC-12 ·", "agents"]
    assert orch.check_sessions(sessions, NOW, lambda key: NOW - 25 * HOUR) == [("WARN", "sessions: ABC-12 still works, 25 hours after its start")]
    with pytest.raises(RuntimeError):
        orch.read_sessions(run=lambda argv: orch.subprocess.CompletedProcess(argv, 1, "", "no server"))


# -- the round's plan, its text and its state file -----------------------------------------------------------


def test_a_session_in_review_holds_no_slot_and_a_fresh_start_does(tmp_path: Path) -> None:
    runner, _, _, clock = runner_of(tmp_path)
    runner.start_run("ABC-9", "start")
    runner.start_run("ABC-10", "start")
    clock.now += 30  # inside the grace time; both sessions alive
    issues = [issue("ABC-9", state="In Review", area="docs"), issue("ABC-10", area="infra"), issue("ABC-5", state="In Progress")]
    assert runner.used(issues, [pr(9, "abc-9-x")]) == {"ABC-10", "ABC-5"}  # ABC-9 waits for its verdict; ABC-5 was started by hand
    clock.now += 200  # past the grace time: a start whose claim never showed holds no slot
    assert runner.used(issues, [pr(9, "abc-9-x")]) == {"ABC-5"}


def test_an_in_progress_fix_round_holds_its_slot_whether_its_session_lives_or_not(tmp_path: Path) -> None:
    runner, host, _, clock = runner_of(tmp_path)
    runner.start_run("ABC-7", "start")
    clock.now += 3600
    issues, prs = [issue("ABC-7", state="In Progress")], [pr(7, "abc-7-x", reviews=ASKED)]  # ready, not yet in draft
    assert runner.used(issues, prs) == {"ABC-7"}  # R1-1: the session reads the verdict within a minute
    host.ended.add("s1")
    assert runner.used(issues, prs) == {"ABC-7"}  # no session shows, and the issue still holds its slot
    assert runner.used([issue("ABC-7", state="In Review")], prs) == set()  # a pull request in review holds none


def test_the_round_names_each_held_slot_and_why_it_counts(tmp_path: Path) -> None:
    """under the round's line, each slot that `building` counts, with its reason; the state file has the same list."""
    runner, _, _, clock = runner_of(tmp_path, max_runs=3)
    runner.start_run("ABC-700", "start")
    clock.now += 30
    issues = [issue("ABC-663", state="In Progress", area="backend"), issue("ABC-700", area="docs"),
              issue("ABC-9", state="In Review", area="infra")]
    said: list[str] = []
    state = tmp_path / "s.json"
    assert orch.round_once(runner.cfg, reads_of(issues, [pr(9, "abc-9-x")]), said.append, runner, dry=True, state_file=state)
    assert unstamped("\n".join(said))[:2] == ["T build 2/3 · review 1 · you 0",
                                               "slots: ABC-663 (In Progress) · ABC-700 (started 1 min ago, not claimed yet)"]
    assert orch.json.loads(state.read_text())["slots"] == [
        {"key": "ABC-663", "reason": "In Progress"}, {"key": "ABC-700", "reason": "started 1 min ago, not claimed yet"}]
    clock.now += 61  # the age counts in whole minutes, up
    plan, _ = runner.plan(issues, [pr(9, "abc-9-x")], dry=True)
    assert plan["slots"][1] == {"key": "ABC-700", "reason": "started 2 min ago, not claimed yet"}
    assert "slots:" not in "\n".join(orch.queue_lines(plan))  # the pinned queue changes only when the queues change
    clock.now += CFG.claim_grace_seconds  # past the grace time: the start holds no slot
    plan, _ = runner.plan(issues, [pr(9, "abc-9-x")], dry=True)
    assert plan["slots"] == [{"key": "ABC-663", "reason": "In Progress"}] and plan["building"] == 1


def test_the_round_prints_no_slots_line_with_no_slot_held(tmp_path: Path) -> None:
    plan, events = plan_of(tmp_path, [issue("ABC-9", state="In Review", area="infra")], [pr(9, "abc-9-x")])
    assert plan["slots"] == [] and plan["building"] == 0
    assert not any(line.startswith("slots") for line in orch.plan_text(plan, events))


def plan_of(tmp_path: Path, issues: list[dict[str, Any]], prs: list[dict[str, Any]], dry: bool = True, **changes: Any) -> Any:
    runner, _, _, _ = runner_of(tmp_path, **changes)
    return runner.plan(issues, prs, dry)


def test_the_plan_groups_the_waits_in_one_queue_per_area(tmp_path: Path) -> None:
    issues = [issue("ABC-386", state="In Review", area="orchestrator"), issue("ABC-388", area="orchestrator"),
              issue("ABC-658", priority="low", area="orchestrator"), issue("ABC-602", size="XS", area="docs"),
              issue("ABC-636", area="docs"), issue("ABC-700", related=("ABC-602",), area="frontend"), issue("ABC-701"),
              issue("ABC-9", state="Escalated", area="infra"), issue("ABC-5", state="In Progress", area="backend")]
    plan, events = plan_of(tmp_path, issues, [pr(386, "abc-386-x"), pr(9, "abc-9-y")], max_runs=3)
    assert [(e.kind, e.key) for e in events] == [("would_start", "ABC-602")]
    assert (plan["building"], plan["max_runs"], plan["reviewing"], orch.your_count(plan)) == (1, 3, 1, 1)
    assert [(q["area"], [h["key"] for h in q["holders"]], [i["key"] for i in q["queue"]], q["next"]) for q in plan["areas"]] == [
        ("backend", ["ABC-5"], [], None),  # ABC-9, in Escalated, holds no Area: infra has no queue
        ("orchestrator", ["ABC-386"], ["ABC-388", "ABC-658"], "ABC-388"), ("docs", ["ABC-602"], ["ABC-636"], "ABC-636")]
    assert plan["other_waits"] == [{"key": "ABC-700", "reason": "related to ABC-602, which runs"},
                                   {"key": "ABC-701", "reason": "no Area label: add one"}]
    assert plan["version"] == orch.PLAN_VERSION and plan["events"] == [{"event": "would_start", "key": "ABC-602",
                                                                                 "model": "claude-sonnet-5-5", "effort": "medium"}]
    text = orch.plan_text(plan, events)
    assert text[1:] == [
        "slots: ABC-5 (In Progress)",
        "would start ABC-602 (claude-sonnet-5-5, medium)",
        "backend       ABC-5",
        "orchestrator  ABC-386 (in review) ▸ ABC-388, ABC-658",
        "docs          ABC-602 (starting) ▸ ABC-636",
        "other waits   ABC-700 related to ABC-602, which runs · ABC-701 no Area label: add one",
        "#386          ABC-386 stale: no review run 20 min after the last commit",  # its last commit is a day old
        "yours         ABC-9 #9 not clear: read the issue"]


def test_the_plan_names_a_wait_for_a_slot_and_a_start_never_claimed(tmp_path: Path) -> None:
    runner, host, _, clock = runner_of(tmp_path, max_runs=1)
    todo = [issue("ABC-635", area="docs"), issue("ABC-645", priority="low", area="frontend")]
    for _ in range(2):
        runner.start_round(todo, [], dry=False)
        host.ended.add(f"s{len(host.starts)}")
        clock.now += 121
    plan, _ = runner.plan(todo, [], dry=True)
    assert plan["other_waits"][0] == {"key": "ABC-635", "reason": "started twice and never claimed: read its last session; to "
                                                                  "start it again, run `owlcode restart` or move it "
                                                                  "out of Todo and back"}
    # a wait for a slot: in its Area's queue (the round's line has the count), or in the other waits with no Area
    issues = [issue("ABC-5", state="In Progress", area="backend"), issue("ABC-635", area="docs"), issue("ABC-636", area="docs")]
    plan, _ = plan_of(tmp_path, issues, [], max_runs=1)
    assert plan["other_waits"] == [] and plan["building"] == 1
    assert [(q["area"], [i["key"] for i in q["queue"]], q["next"]) for q in plan["areas"]] == [
        ("backend", [], None), ("docs", ["ABC-635", "ABC-636"], "ABC-635")]
    assert "docs     ▸ ABC-635, ABC-636" in orch.plan_text(plan, [])
    plan, _ = plan_of(tmp_path, [issue("ABC-5", state="In Progress"), issue("ABC-635")], [], max_runs=1, distinct_areas=False)
    assert plan["other_waits"] == [{"key": "ABC-635", "reason": "no free slot"}]


def test_a_blocked_issue_queues_after_its_blocker_or_carries_a_mark(tmp_path: Path) -> None:
    issues = [issue("ABC-602", size="XS", area="docs"),
              issue("ABC-610", priority="high", blocked=(("ABC-611", "Todo"),), area="docs"),  # after ABC-611 in the queue: no mark
              issue("ABC-611", area="docs"), issue("ABC-612", priority="urgent", blocked=(("ABC-602", "Todo"),), area="docs"),
              issue("ABC-613", priority="urgent", blocked=(("ABC-5", "In Progress"),), area="docs"),
              issue("ABC-614", priority="urgent", blocked=(("ABC-40", "Backlog"),), area="docs"),
              issue("ABC-615", priority="urgent", blocked=(("ABC-9", "In Review"),), area="docs"),
              issue("ABC-5", state="In Progress", area="backend"), issue("ABC-9", state="In Review", area="infra")]
    plan, _ = plan_of(tmp_path, issues, [pr(9, "abc-9-x")], max_runs=5)
    docs = next(q for q in plan["areas"] if q["area"] == "docs")
    assert [h["key"] for h in docs["holders"]] == ["ABC-602"]
    assert [(i["key"], i["mark"]) for i in docs["queue"]] == [
        ("ABC-612", None), ("ABC-613", "after ABC-5 (backend)"), ("ABC-614", "after ABC-40 (Backlog)"),
        ("ABC-615", "stacks on ABC-9"), ("ABC-611", None), ("ABC-610", None)]
    assert docs["next"] == "ABC-612"
    assert docs["queue"][1]["blockers"] == [{"key": "ABC-5", "state": "In Progress", "area": "backend"}]
    assert plan["stacks"] == [{"key": "ABC-615", "on": "ABC-9"}]
    assert "docs          ABC-602 (starting) ▸ ABC-612, ABC-613 after ABC-5 (backend), ABC-614 after ABC-40 (Backlog), " \
           "ABC-615 stacks on ABC-9, ABC-611, ABC-610" in orch.plan_text(plan, [])
    # a queue whose every issue is marked has no next start
    plan, _ = plan_of(tmp_path, [issue("ABC-613", blocked=(("ABC-5", "In Progress"),), area="docs"), issue("ABC-5", state="In Progress", area="backend")], [])
    assert next(q for q in plan["areas"] if q["area"] == "docs")["next"] is None


def test_the_plan_holds_no_issue_text(tmp_path: Path) -> None:
    secret = "Private words of the issue"
    issues = [issue("ABC-2", area="docs", description=secret), issue("ABC-3", area="docs", description=secret)]
    plan, _ = plan_of(tmp_path, [dict(i, title=secret) for i in issues], [])
    assert secret not in orch.json.dumps(plan)


def test_the_state_file_round_trips_and_is_replaced_whole(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plan, _ = plan_of(tmp_path, [issue("ABC-2", area="docs"), issue("ABC-3", area="docs")], [])
    path = tmp_path / "state" / "state.json"
    orch.write_state(path, plan)
    assert orch.json.loads(path.read_text()) == plan
    before = path.read_text()

    def broken(_src: Any, _dst: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(orch.os, "replace", broken)
    with pytest.raises(OSError):
        orch.write_state(path, dict(plan, building=9))
    assert path.read_text() == before and [p.name for p in path.parent.iterdir()] == ["state.json"]  # no half file, no temp file


def test_the_loop_prints_only_changes_and_a_heartbeat_after_fifteen_quiet_minutes(tmp_path: Path) -> None:
    """a round prints when its plan changed or it has an event; a dry run's repeated `would start` is no event."""
    runner, _, _, clock = runner_of(tmp_path)
    said: list[str] = []
    state = tmp_path / "s.json"
    todo = [issue("ABC-2", area="docs"), issue("ABC-3", area="docs")]

    def loop(issues: Any, rounds: int = 1) -> list[str]:
        said.clear()
        for _ in range(rounds):
            orch.round_once(runner.cfg, reads_of(issues, []), said.append, runner, dry=True, state_file=state, only_changes=True)
            clock.now += 60
        return said

    assert loop(todo, 15) == [
        "2026-11-02T10:00:00Z build 0/2 · review 0 · you 0", "would start ABC-2 (claude-sonnet-5-5, medium)", "docs  ABC-2 (starting) ▸ ABC-3",
        '{"event": "would_start", "key": "ABC-2", "model": "claude-sonnet-5-5", "effort": "medium", "time": "2026-11-02T10:00:00Z"}']  # then 14 quiet rounds print nothing
    assert loop(todo) == ["2026-11-02T10:15:00Z no change since 2026-11-02T10:00:00Z"]  # 15 quiet minutes
    assert loop(todo, 14) == []
    assert loop(todo) == ["2026-11-02T10:30:00Z no change since 2026-11-02T10:00:00Z"]
    assert "docs  ABC-3 (starting)" in loop(todo[1:])  # the queue changed: it prints again
    assert loop(OSError("down"), 2) == ["2026-11-02T10:32:00Z round failed: OSError: down",
                                        "2026-11-02T10:33:00Z round failed: OSError: down"]  # a failed round prints every time
    assert loop(todo[1:])[0] == "2026-11-02T10:34:00Z build 0/2 · review 0 · you 0"  # and the next good round prints in full
    assert orch.json.loads(state.read_text())["areas"][0]["holders"] == [{"key": "ABC-3", "state": "starting", "kind": "building"}]
    real = runner.plan

    def with_event(*args: Any) -> Any:  # an event that is no dry run's repeat prints, whatever the plan
        plan, events = real(*args)
        failed = orch.Event("escalation_failed", "ABC-9", "escalation of ABC-9 failed: OSError: x", "OSError")
        return dict(plan, events=[*plan["events"], {"event": failed.kind, "key": "ABC-9"}]), [*events, failed]

    runner.plan = with_event
    for _ in range(2):
        assert "escalation of ABC-9 failed: OSError: x" in loop(todo[1:])


def test_a_state_file_that_cannot_be_written_fails_the_round_after_the_plan_printed(tmp_path: Path) -> None:
    said: list[str] = []
    (tmp_path / "file").write_text("")
    assert orch.round_once(CFG, reads_of([], []), said.append, state_file=tmp_path / "file" / "state.json") is False
    assert said[-1].startswith("state file not written: ") and " build 0/2 " in said[0]


# -- The pinned queue: one comment edited in place, with a fake Linear ------------------------------------------

class FakeComments:
    """Linear's comments of the queue issue: (id, body, updatedAt) each, with `fail` to refuse the next call."""

    def __init__(self, held: list[tuple[str, str, str]] | None = None) -> None:
        self.held = list(held or [])
        self.calls: list[tuple[str, ...]] = []
        self.fail = ""

    def _check(self, name: str) -> None:
        if self.fail == name:
            raise RuntimeError("Linear did not answer")

    def comments(self) -> Any:
        def find(key: str, mark: str) -> tuple[str, str, str] | None:
            self._check("find")
            self.calls.append(("find", key))
            own = [c for c in self.held if c[1].startswith(mark)]
            return own[-1] if own else None

        def update(comment: str, body: str) -> None:
            self._check("update")
            self.calls.append(("update", comment))
            if comment not in [c[0] for c in self.held]:
                raise RuntimeError("no such comment")
            self.held = [(i, body if i == comment else b, at) for i, b, at in self.held]

        def create(key: str, body: str) -> str:
            self._check("create")
            self.calls.append(("create", key))
            self.held.append((f"c{len(self.held) + 1}", body, ""))
            return f"c{len(self.held)}"

        return orch.Comments(find=find, update=update, create=create)


def queue_runner(tmp_path: Path, fake: FakeComments | None) -> tuple[Any, Clock]:
    runner, _, _, clock = runner_of(tmp_path, queue_issue="ABC-900")
    runner.queue = orch.PinnedComment("ABC-900", orch.QUEUE_MARK, 5 * 60, fake.comments() if fake else None)
    return runner, clock


def queue_round(runner: Any, issues: list[dict[str, Any]], dry: bool = True) -> list[str]:
    said: list[str] = []
    assert orch.round_once(runner.cfg, reads_of(issues, []), said.append, runner, dry=dry)
    return said


def test_the_queue_is_one_comment_edited_in_place(tmp_path: Path) -> None:
    fake = FakeComments()
    runner, clock = queue_runner(tmp_path, fake)
    said = queue_round(runner, [issue("ABC-2", area="docs"), issue("ABC-3", area="docs")])
    assert said[-1] == "wrote the queue to ABC-900" and fake.calls == [("find", "ABC-900"), ("create", "ABC-900")]
    body = fake.held[0][1]
    assert body.startswith("Orchestrator queue at 2026-11-02T10:00:00Z.")
    assert orch.block_lines(body) == ["build 0/2 · review 0 · you 0", "docs  ABC-2 (starting) ▸ ABC-3"]
    clock.now += 5 * 60
    queue_round(runner, [issue("ABC-2", state="In Progress", area="docs"), issue("ABC-3", area="docs")])
    assert fake.calls[2:] == [("update", "c1")] and len(fake.held) == 1  # the same comment, never a second one
    assert orch.block_lines(fake.held[0][1]) == ["build 1/2 · review 0 · you 0", "docs  ABC-2 ▸ ABC-3"]


def test_the_queue_writes_nothing_when_nothing_changed(tmp_path: Path) -> None:
    fake = FakeComments()
    runner, clock = queue_runner(tmp_path, fake)
    todo = [issue("ABC-2", area="docs")]
    queue_round(runner, todo)
    for _ in range(3):
        clock.now += 10 * 60  # past the rate limit: only the unchanged lines keep it from a write
        assert "wrote the queue to ABC-900" not in queue_round(runner, todo)
    assert fake.calls == [("find", "ABC-900"), ("create", "ABC-900")]


def test_the_queue_writes_at_most_once_per_queue_write_minutes(tmp_path: Path) -> None:
    fake = FakeComments()
    runner, clock = queue_runner(tmp_path, fake)
    queue_round(runner, [issue("ABC-2", area="docs")])
    clock.now += 4 * 60 + 59
    assert "wrote the queue to ABC-900" not in queue_round(runner, [issue("ABC-3", area="docs")])  # a change, too soon
    assert [c[0] for c in fake.calls] == ["find", "create"]
    clock.now += 1
    assert "wrote the queue to ABC-900" in queue_round(runner, [issue("ABC-3", area="docs")])  # the change waited, then went
    assert [c[0] for c in fake.calls] == ["find", "create", "update"] and "ABC-3" in fake.held[0][1]


def test_after_a_restart_the_queue_edits_its_old_comment_and_keeps_the_rate_limit(tmp_path: Path) -> None:
    lines = ["build 0/2 · review 0 · you 0", "docs  ABC-2 (starting)"]
    other = ("c1", "A comment of the owner", "2026-11-01T09:00:00Z")
    old = ("c2", orch.comment_body(orch.QUEUE_MARK, lines, "2026-11-02T09:58:00Z"), "2026-11-02T09:58:00Z")
    fake = FakeComments([other, old])
    runner, clock = queue_runner(tmp_path, fake)
    assert "wrote the queue to ABC-900" not in queue_round(runner, [issue("ABC-2", area="docs")])  # the same lines
    assert "wrote the queue to ABC-900" not in queue_round(runner, [issue("ABC-3", area="docs")])  # 2 min after the last write
    clock.now += 3 * 60
    queue_round(runner, [issue("ABC-3", area="docs")])
    assert fake.calls == [("find", "ABC-900"), ("update", "c2")] and fake.held[0] == other


def test_a_failed_queue_write_prints_its_type_and_tries_again(tmp_path: Path) -> None:
    fake = FakeComments()
    runner, clock = queue_runner(tmp_path, fake)
    fake.fail = "find"
    assert queue_round(runner, [issue("ABC-2", area="docs")])[-1] == "queue: reading ABC-900 failed: RuntimeError"
    assert fake.calls == []  # no create without the read: it would make a second comment
    fake.fail = ""
    queue_round(runner, [issue("ABC-2", area="docs")])
    fake.held.clear()  # the owner deleted the comment
    clock.now += 5 * 60
    assert queue_round(runner, [issue("ABC-3", area="docs")])[-1] == "queue: writing to ABC-900 failed: RuntimeError"
    queue_round(runner, [issue("ABC-3", area="docs")])
    assert [c[0] for c in fake.calls] == ["find", "create", "update", "find", "create"] and len(fake.held) == 1


def test_a_dry_run_prints_the_queue_it_would_write(tmp_path: Path) -> None:
    runner, clock = queue_runner(tmp_path, None)
    said = queue_round(runner, [issue("ABC-2", area="docs")])
    at = said.index("would write the queue to ABC-900:")
    assert said[at + 1:] == ["Orchestrator queue at 2026-11-02T10:00:00Z. The orchestrator edits this comment in place.", "",
                             "```", "build 0/2 · review 0 · you 0", "docs  ABC-2 (starting)", "```"]
    clock.now += 10 * 60
    assert not any(line.startswith("would write") for line in queue_round(runner, [issue("ABC-2", area="docs")]))


def test_no_queue_issue_writes_no_queue(tmp_path: Path) -> None:
    assert CFG.queue_issue == "" and orch.make_queue(CFG, dry=False) is None
    runner, _, _, _ = runner_of(tmp_path)
    assert not any("queue" in line for line in queue_round(runner, [issue("ABC-2", area="docs")]))
    cfg = orch.make_queue(fix_cfg(tmp_path, queue_issue="ABC-900", queue_write_minutes=2), dry=True)
    assert (cfg.issue, cfg.min_seconds, cfg.comments) == ("ABC-900", 120, None)


def test_the_queue_holds_no_issue_text(tmp_path: Path) -> None:
    secret = "Private words of the issue"
    fake = FakeComments()
    runner, _ = queue_runner(tmp_path, fake)
    queue_round(runner, [dict(issue("ABC-2", area="docs", description=secret), title=secret)])
    assert fake.held and secret not in fake.held[0][1]


def test_config_reads_the_queue_settings(tmp_path: Path) -> None:
    cfg = orch.load_config(config_with(tmp_path, queue_issue='queue_issue = "ABC-900"', queue_write_minutes="queue_write_minutes = 10"))
    assert (cfg.queue_issue, cfg.queue_write_minutes) == ("ABC-900", 10)
    with pytest.raises(orch.ConfigError, match="queue_issue"):
        orch.load_config(config_with(tmp_path, queue_issue='queue_issue = "ABC-900; rm -rf"'))


def test_real_comments_finds_its_own_newest_comment_and_edits_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[str, dict[str, Any]]] = []

    class FakeLinear:
        def post(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
            sent.append((query.split("(")[0].split()[-1], variables))
            if "commentUpdate" in query:
                return {"commentUpdate": {"success": True}}
            if "commentCreate" in query:
                return {"commentCreate": {"success": True, "comment": {"id": "new"}}}
            return {"issue": {"comments": {"nodes": [
                {"id": "a", "body": "Orchestrator queue at x", "updatedAt": "2026-11-02T09:00:00Z", "user": {"isMe": True}},
                {"id": "b", "body": "Orchestrator queue at y", "updatedAt": "2026-11-02T09:30:00Z", "user": {"isMe": True}},
                {"id": "c", "body": "Orchestrator queue by hand", "updatedAt": "2026-11-02T09:50:00Z", "user": {"isMe": False}}]}}}

        def issue(self, key: str) -> dict[str, Any]:
            return {"id": f"uuid-{key}"}

    monkeypatch.setattr(orch, "linear_api", lambda cfg, environ=None: FakeLinear())
    comments = orch.real_comments(CFG)
    assert comments.find("ABC-900", orch.QUEUE_MARK) == ("b", "Orchestrator queue at y", "2026-11-02T09:30:00Z")
    comments.update("b", "text")
    assert comments.create("ABC-900", "text") == "new"
    assert [name for name, _ in sent] == ["Comments", "Edit", "Comment"] and sent[2][1]["id"] == "uuid-ABC-900"


# -- closing the workspaces of finished runs ------------------------------------------------------------------

HOUR = 3600
FINAL = ("Done", "Canceled", "Duplicate")


def space(label: str = "ABC-7 ·", status: str = "done", workspace: str = "w7") -> dict[str, Any]:
    return {"workspace_id": workspace, "label": label, "agent_status": status, "number": 3}


def closes(spaces: list[dict[str, Any]], state: str = "Done", hours: float = 13, key: str = "ABC-7") -> list[Any]:
    return orch.to_close(spaces, {key: {"state": state, "since": NOW - hours * HOUR}}, NOW, 12, FINAL)


def test_a_finished_run_closes_12_hours_after_its_issue_is_done_or_canceled() -> None:
    assert closes([space()]) == [orch.Closing("w7", "ABC-7", "Done", 13)]
    assert closes([space(status="idle")], state="Canceled", hours=12) == [orch.Closing("w7", "ABC-7", "Canceled", 12)]


@pytest.mark.parametrize("label", ["agents", "abc-7", "ABC-7", "abc-7 ·", "my ABC-7 ·", "ABC-7 · #"])
def test_a_workspace_the_orchestrator_did_not_label_stays(label: str) -> None:
    assert closes([space(label=label)]) == []


def test_a_workspace_labelled_before_the_install_closes_too() -> None:
    assert [c.key for c in closes([space(label="abc-7-start-20261109T220000")])] == ["ABC-7"]


@pytest.mark.parametrize("state", ["In Progress", "In Review", "Approved", "Escalated", "Todo", "Backlog"])
def test_an_issue_that_is_not_final_keeps_its_workspace(state: str) -> None:
    assert closes([space()], state=state) == []


@pytest.mark.parametrize("status", ["working", "blocked", "unknown", ""])
def test_an_agent_that_is_not_idle_or_done_keeps_its_workspace(status: str) -> None:
    assert closes([space(status=status)]) == []


def test_an_issue_final_for_less_than_the_hours_or_with_no_time_keeps_its_workspace() -> None:
    assert closes([space()], hours=11) == []
    assert closes([space()], hours=11.99) == []
    assert orch.to_close([space()], {"ABC-7": {"state": "Done", "since": None}}, NOW, 12, FINAL) == []
    assert orch.to_close([space()], {}, NOW, 12, FINAL) == []  # an issue that was not read
    assert orch.to_close([space()], {"ABC-7": {"state": "Done", "since": NOW - 99 * HOUR}}, NOW, 0, FINAL) == []  # 0: off


def test_the_setting_close_after_hours_is_12_in_the_example_and_0_or_more(tmp_path: Path) -> None:
    assert CFG.close_after_hours == 12
    with pytest.raises(orch.ConfigError, match="close_after_hours"):
        orch.load_config(config_with(tmp_path, close_after_hours="close_after_hours = -1"))
    assert orch.load_config(config_with(tmp_path, close_after_hours="close_after_hours = 0")).close_after_hours == 0


def test_the_retired_conflict_marker_and_half_a_conflict_pair_are_refused(tmp_path: Path) -> None:
    text = EXAMPLE_TEXT
    path = tmp_path / "c.toml"
    path.write_text(text.replace("[github]", "[github]\nconflict_marker='The branch update found'"))
    with pytest.raises(orch.ConfigError, match="conflict_marker is retired: set conflict_comment and conflict_author"):
        orch.load_config(path)
    for lines in ({"conflict_comment": "conflict_comment = ''"}, {"conflict_author": "conflict_author = ''"}):
        with pytest.raises(orch.ConfigError, match="conflict_comment and conflict_author go together"):
            orch.load_config(config_with(tmp_path, **lines))
    both_off = orch.load_config(config_with(tmp_path, conflict_comment="conflict_comment = ''", conflict_author="conflict_author = ''"))
    assert both_off.conflict_comment == "" == both_off.conflict_author


def test_the_herdr_host_lists_and_closes_workspaces_and_a_failed_close_raises() -> None:
    calls: list[list[str]] = []
    reply = '{"result":{"type":"workspace_list","workspaces":[{"workspace_id":"w7","label":"ABC-7 ·","agent_status":"done"}]}}'
    code = {"close": 0}

    def run(argv: Any) -> Any:
        calls.append(list(argv))
        if argv[2] == "close":
            return orch.subprocess.CompletedProcess(argv, code["close"], "{}", "" if code["close"] == 0 else "no such workspace")
        return orch.subprocess.CompletedProcess(argv, 0, reply, "")

    host = orch.HerdrHost(run)
    assert [s["label"] for s in host.workspaces()] == ["ABC-7 ·"]
    host.close("w7")
    assert calls == [["herdr", "workspace", "list"], ["herdr", "workspace", "close", "w7"]]
    code["close"] = 1
    with pytest.raises(RuntimeError, match="no such workspace"):
        host.close("w7")


def test_the_process_host_lists_no_workspace(tmp_path: Path) -> None:
    assert orch.ProcessHost(tmp_path).workspaces() == []


# -- the Paseo host: `paseo ls --json` items as the CLI 0.15.4 prints them ------------------------------------

PASEO_COMMAND = ('paseo run --no-wait --quiet --cwd {cwd} --title {label} --label orchestrator={key} --label profile={profile}{env}{workers_env} --provider claude '
                 '--model {model} --thinking {effort} --mode {permission} {prompt}')
PI_PASEO_COMMAND = ('paseo run --no-wait --quiet --cwd {cwd} --title {label} --label orchestrator={key} --label profile={profile}{env}{workers_env} --provider pi '
                    '--model {model} --thinking {effort} {prompt}')
PASEO_AGENTS = ('[{"id": "a1b2c3d4-0001", "shortId": "a1b2c3", "name": "ABC-7 ·", "provider": "claude",'
                ' "thinking": "high", "status": "running", "cwd": "/clone", "created": "2026-11-09T22:00:00Z"},'
                ' {"id": "a1b2c3d4-0002", "shortId": "a1b2c4", "name": "ABC-8 ·", "provider": "claude",'
                ' "thinking": "medium", "status": "idle", "cwd": "/clone", "created": "2026-11-09T22:00:00Z"},'
                ' {"id": "a1b2c3d4-0003", "shortId": "a1b2c5", "name": "ABC-9 ·", "provider": "claude",'
                ' "thinking": "medium", "status": "error", "cwd": "/clone", "created": "2026-11-09T22:00:00Z"},'
                ' {"id": "a1b2c3d4-0004", "shortId": "a1b2c6", "name": "ABC-6 ·", "provider": "claude",'
                ' "thinking": "medium", "status": "closed", "cwd": "/clone", "created": "2026-11-08T22:00:00Z"},'
                ' {"id": "a1b2c3d4-0005", "shortId": "a1b2c7", "name": "-", "provider": "claude",'
                ' "thinking": "medium", "status": "running", "cwd": "/Users/owner", "created": "2026-11-09T21:00:00Z"}]')


PASEO_WORKSPACES = ('[{"workspaceId": "wks_1", "name": "ABC-8", "isolation": "local"},'
                    ' {"workspaceId": "wks_2", "name": "ABC-7", "isolation": "local"},'
                    ' {"workspaceId": "wks_3", "name": "main", "isolation": "local"}]')


class FakePaseo:
    """`run` for the Paseo host: it records each argv and answers by the command (`run`, `ls`, `logs`, `archive`)."""

    def __init__(self, **codes: int) -> None:
        self.calls: list[list[str]] = []
        self.codes = codes
        self.replies = {"run": "a1b2c3d4-0001\n", "ls": PASEO_AGENTS, "logs": "entry 1\nentry 2\n", "archive": "",
                        "workspace-create": "new\n", "workspace-ls": PASEO_WORKSPACES, "workspace-archive": ""}

    def __call__(self, argv: Any) -> Any:
        self.calls.append(list(argv))
        verb = "workspace-" + argv[2] if argv[1] == "workspace" else argv[1]
        code = self.codes.get(verb, 0)
        return orch.subprocess.CompletedProcess(argv, code, self.replies[verb] if code == 0 else "", "" if code == 0 else "daemon down")


def test_the_example_paseo_command_fills_every_place_and_quotes_the_folder() -> None:
    command = orch.command_for(PASEO_COMMAND, "ABC-7", "start", "opus", "high", "ABC-7 ·", "/Users/a b/clone", "20261119T230614Z-mini",
                               "claude", permission="bypassPermissions", prompt=EXAMPLE.skill_prompt)
    assert command == ("paseo run --no-wait --quiet --cwd '/Users/a b/clone' --title 'ABC-7 ·' "
                       "--label orchestrator=ABC-7 --label profile=claude --provider claude --model opus --thinking high --mode bypassPermissions "
                       "'/implement ABC-7 start 20261119T230614Z-mini'")
    # one pass: a folder that names a place stays as it is, and a shell word in it stays quoted
    assert orch.command_for("x {cwd} {key}", "ABC-7", "start", cwd="/a/{key}$(x)") == "x '/a/{key}$(x)' ABC-7"
    for label, cwd in (("abc-7; rm -rf ~", "/clone"), ("", "/clone"), ("ABC-7 ·", "")):
        with pytest.raises(ValueError):
            orch.command_for(PASEO_COMMAND, "ABC-7", "start", "opus", "high", label, cwd, "20261119T230614Z-mini", "claude", permission="auto",
                             prompt=EXAMPLE.skill_prompt)
    with pytest.raises(ValueError):  # the profile goes into the label: a name that is no setting shape is refused
        orch.command_for(PASEO_COMMAND, "ABC-7", "start", "opus", "high", "ABC-7 ·", "/clone", "20261119T230614Z-mini", "x; rm",
                         permission="auto", prompt=EXAMPLE.skill_prompt)
    assert orch.command_for("x {key}", "ABC-7", "start") == "x ABC-7"  # a template without them needs neither


def test_preflight_checks_each_pi_cell_and_the_review_without_running_the_template(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = config_with(tmp_path, inference_profile='inference_profile = "pi-chatgpt"', session_host='session_host = "paseo"',
                       run_cwd=f'run_cwd = "{tmp_path}"')
    cfg = orch.load_config(path)
    checked: list[str] = []

    def model_exists(model: str) -> bool:
        checked.append(model)
        return True

    fake = dataclasses.replace(probes(), model=model_exists, paseo_profile=lambda: ("pi-chatgpt",))
    for command in ("--preflight", "--check"):
        checked.clear()
        assert orch.main(["--config", str(path), command], probes=fake) == 0
        assert checked == ["openai-codex/gpt-6.1-terra"] * 2 + ["openai-codex/gpt-6.1-sol"] * 3
        assert "ok   pi-chatgpt review: openai-codex/gpt-6.1-sol is available" in capsys.readouterr().out
    assert any(level == orch.FAIL and "review:" in line
               for level, line in orch.preflight(cfg, lambda: True, lambda _: False, lambda: ("pi-chatgpt",)))
    assert any(level == orch.FAIL and "sign-in" in line
               for level, line in orch.preflight(cfg, lambda: False, model_exists, lambda: ("pi-chatgpt",)))


def test_pi_offline_lookup_needs_the_exact_provider_and_model(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = orch.load_config(config_with(tmp_path, inference_profile='inference_profile = "pi-ollama"', session_host='session_host = "paseo"'))
    monkeypatch.setenv("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")

    def cli(argv: list[str]) -> Any:
        assert argv[0] == "/usr/bin/env"
        assert argv[1] == f"PATH={Path.home()}/.local/bin:{Path.home()}/.local/share/mise/shims:/usr/bin:/bin:/usr/sbin:/sbin"
        # every pi provider's dummy key: a cell may run on another pi profile than the one in force
        assert argv[2:] == ["LITHOSAI_API_KEY=offline-preflight", "OLLAMA_API_KEY=offline-preflight", "pi", "--offline",
                            "--no-approve", "--list-models"]
        return orch.subprocess.CompletedProcess(argv, 0, "pi-ollama glm-5.3 128K 16K yes no\n", "")

    monkeypatch.setattr(orch, "run_quiet", cli)
    assert orch.pi_model_exists(cfg, "pi-ollama/glm-5.3")
    assert not orch.pi_model_exists(cfg, "pi-ollama/glm-5")


@pytest.mark.parametrize(("host", "profile", "template"), [
    ("herdr", "claude", 'claude --profile {profile}{review_flag}{workers_flag} --permission-mode {permission} --model {model} --effort {effort} {prompt}'),
    ("process", "claude", '/usr/bin/python3 -I {wrapper} session --profile {profile}{review_flag}{workers_flag} --keep-cwd --print '
                          '--output-format stream-json --verbose --permission-mode {permission} --model {model} --effort {effort} {prompt}'),
    ("herdr", "pi-chatgpt", 'pi --profile {profile}{review_flag}{workers_flag} --model {model} --thinking {effort} {prompt}'),
    ("paseo", "claude", PASEO_COMMAND),
    ("paseo", "pi-chatgpt", PI_PASEO_COMMAND),
])
def test_each_supported_pair_uses_its_command_and_check_previews_it(tmp_path: Path, capsys: pytest.CaptureFixture[str],
                                                                    host: str, profile: str, template: str) -> None:
    path = config_with(tmp_path, session_host=f'session_host = "{host}"', inference_profile=f'inference_profile = "{profile}"',
                       run_cwd='run_cwd = "/a b/clone"')
    cfg = orch.load_config(path)
    assert cfg.run_command == template
    assert "LOCAL_REVIEW_COMMAND" not in cfg.run_command  # only agent_user.py sets it
    orch.main(["--config", str(path), "--check"], probes=probes())
    command = orch.command_for(template, "EXAMPLE-1", "start", *cfg.run_cells[0], "EXAMPLE-1 ·", "/a b/clone", "20000101T000000Z-example",
                               profile=profile, permission=cfg.permission_mode, prompt=cfg.skill_prompt, review_profile=profile,
                               hide=orch.hidden_keys(cfg, profile) if host == "paseo" else (), workers=orch.pytest_workers(cfg.max_runs))
    assert f"ok   example start command: {command}\n" in capsys.readouterr().out
    if host == "process":
        assert shlex.split(command)[2] == str(REAL_REPO / ".workflow" / "agent_user.py")


def test_preview_reports_an_unknown_home_directory(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = config_with(tmp_path, run_cwd='run_cwd = "~no-such-orchestrator-user/clone"')
    assert orch.main(["--config", str(path), "--check"], probes=probes()) == 1
    text = capsys.readouterr().out
    assert "FAIL example start command: cannot expand the home directory in run_cwd" in text
    assert "ok   inference profile: claude" in text


def test_changing_only_inference_profile_changes_the_paseo_command(tmp_path: Path) -> None:
    path = config_with(tmp_path, session_host='session_host = "paseo"')
    before = orch.check_command(orch.load_config(path))[1]
    path.write_text(path.read_text().replace('inference_profile = "claude"', 'inference_profile = "pi-chatgpt"'))
    after = orch.check_command(orch.load_config(path))[1]
    assert "--provider claude --model claude-sonnet-5-5 --thinking medium --mode auto" in before
    assert "--provider pi --model openai-codex/gpt-6.1-terra --thinking medium" in after
    assert " --mode " not in after and "/skill:implement EXAMPLE-1 start " in after


def test_the_label_of_a_first_start_and_of_a_retry(tmp_path: Path) -> None:
    assert orch.session_label("abc-668") == "ABC-668 ·" and orch.session_label("ABC-668", 2) == "ABC-668 · #2"
    host = FakeHost()
    runner = orch.Runner(fix_cfg(tmp_path), host, None, Clock())
    runner.start_run("ABC-668", "start")
    runner.tries["ABC-668"] = 1  # the round counts a start that opened a session
    runner.start_run("ABC-668", "start")
    assert [label for label, _, _ in host.starts] == ["ABC-668 ·", "ABC-668 · #2"]


def test_own_key_reads_our_labels_and_no_others() -> None:
    assert orch.own_key("ABC-668 ·") == "ABC-668" and orch.own_key("ABC-668 · #2") == "ABC-668"
    assert orch.own_key("abc-668") is None and orch.own_key("ABC-668") is None and orch.own_key("abc-668 ·") is None
    assert orch.own_key("abc-7-start-20261109T220000") == "ABC-7" and orch.own_key("abc-7-start-2026") is None
    assert orch.own_key("my ABC-668 ·") is None and orch.own_key("") is None and orch.own_key(None) is None


@pytest.mark.real_clock("a real process runs the fake run_command")
def test_the_process_host_names_each_start_of_one_key_apart(tmp_path: Path) -> None:
    host = orch.ProcessHost(tmp_path / "logs")
    command = "echo '{\"type\":\"system\",\"subtype\":\"init\"}'"
    first, second = host.start("ABC-7 ·", command, str(tmp_path)), host.start("ABC-7 ·", command, str(tmp_path))
    assert first != second and re.fullmatch(r"ABC-7-\d{8}T\d{6}(-\d+)?", first) and re.fullmatch(r"ABC-7-\d{8}T\d{6}(-\d+)?", second)
    for session in (first, second):
        host.procs[session].wait(timeout=30)
        assert (tmp_path / "logs" / f"{session}.out").exists()


def test_a_start_gives_the_command_the_label_and_the_folder_it_gives_the_host(tmp_path: Path) -> None:
    host = FakeHost()
    runner = orch.Runner(fix_cfg(tmp_path, run_command="run-it {key} {label} {cwd}"), host, None, Clock())
    runner.start_run("ABC-7", "start")
    [(label, command, cwd)] = host.starts
    assert label == "ABC-7 ·" and cwd == str(tmp_path)
    assert command == f"run-it ABC-7 {orch.shlex.quote(label)} {orch.shlex.quote(str(tmp_path))}"


def test_paseo_reads_an_agent_in_error_as_a_failed_start() -> None:
    """a start whose agent shows `error` is a setup failure, and a list that fails raises so no halt starts."""
    host = orch.PaseoHost(FakePaseo())
    assert host.failed("a1b2c3d4-0003") and not host.failed("a1b2c3d4-0001") and not host.failed("gone")
    with pytest.raises(RuntimeError, match="daemon down"):
        orch.PaseoHost(FakePaseo(ls=1)).failed("a1b2c3d4-0003")
    assert not orch.HerdrHost().failed("ABC-7") and not orch.ProcessHost(Path("logs")).failed("ABC-7")


def test_the_paseo_host_starts_an_agent_and_reads_its_id() -> None:
    run = FakePaseo()
    host = orch.PaseoHost(run)
    assert host.start("ABC-7 ·", "paseo run --quiet x", "/Users/a b/clone") == "a1b2c3d4-0001"
    assert run.calls == [["paseo", "workspace", "create", "--isolation", "local", "--path", "/Users/a b/clone", "--title", "ABC-7", "-q"],
                         ["paseo", "run", "--workspace", "new", "--quiet", "x"]]
    for reply in ("", "a1\na2\n", "--help\n", "id with space\n"):  # no id, two ids, an option, a word that is no id
        run.replies["run"] = reply
        # `run` exited 0: the agent exists, so the start counts and the label stands for the id
        assert host.start("ABC-7 ·", "paseo run --quiet x", "/clone") == "ABC-7 ·"
    failing = FakePaseo(run=1)
    failing.replies["ls"] = "[]"
    with pytest.raises(RuntimeError, match="daemon down"):
        orch.PaseoHost(failing).start("ABC-7 ·", "paseo run --quiet x", "/clone")


@pytest.mark.parametrize(("session_workspace", "archive_attempts"), [("old", 2), ("new", 0), ("late", 1)])
def test_paseo_retries_an_empty_workspace_by_exact_id_while_halted(tmp_path: Path, session_workspace: str, archive_attempts: int) -> None:
    fake = FakePaseo(run=1, **{"workspace-archive": 1})
    fake.replies["ls"] = json.dumps([{"id": "agent", "name": "ABC-7 ·", "status": "closed", "workspaceId": session_workspace}])
    host = orch.PaseoHost(fake)
    runner = orch.Runner(fix_cfg(tmp_path, run_command="paseo run --no-wait --quiet x"), host, None, Clock())
    data = orch.Reads(lambda: [issue("ABC-7", area="docs")], list)
    assert orch.round_once(runner.cfg, data, lambda _: None, runner, dry=False)
    if session_workspace == "new":
        assert not runner.halt.cause and runner.tries == {"ABC-7": 1}
    else:
        assert runner.halt.cause and not runner.tries and host.empty_starts == {"new"}
    fake.codes["workspace-archive"] = 0
    if session_workspace == "late":  # an agent opened in the workspace before the retry
        fake.replies["ls"] = '[{"id":"owner-agent","name":"Owner","status":"idle","workspaceId":"new"}]'
    assert orch.round_once(runner.cfg, data, lambda _: None, runner, dry=False)
    assert not host.empty_starts
    archived = [c[-1] for c in fake.calls if c[:3] == ["paseo", "workspace", "archive"]]
    assert archived == ["new"] * archive_attempts


@pytest.mark.parametrize("session_exists", [True, False])
def test_paseo_late_confirmation_checks_the_launch_before_retrying(tmp_path: Path, session_exists: bool) -> None:
    fake = FakePaseo(run=1)
    fake.replies["ls"] = "[]"
    readable = False

    def run(argv: list[str]) -> Any:
        if "--background" in argv and not readable:
            raise OSError("Paseo unavailable")
        return fake(argv)

    runner, _, writes, clock = runner_of(tmp_path, session_host="paseo", run_command="paseo run --no-wait --quiet x")
    runner.host = orch.PaseoHost(run)
    data = orch.Reads(lambda: [issue("ABC-7", area="docs")], list)
    notices, log = [], []
    runner.writes = dataclasses.replace(writes.writes(), notice=lambda cause, at: notices.append(cause))
    runner.halt.check = lambda: pytest.fail("a pending or confirmed launch does not need preflight")
    state = tmp_path / "state.json"
    assert orch.round_once(runner.cfg, data, log.append, runner, dry=False, state_file=state)
    assert runner.confirm_launch and not (runner.tries or writes.done)
    for _ in range(2):
        clock.now += 60 * runner.cfg.halt_check_minutes
        assert orch.round_once(runner.cfg, data, log.append, runner, dry=False, state_file=state)
        assert "Inspect the launch workspace" in json.loads(state.read_text())["halted"]
    assert len(notices) == 2 and "confirmation or cleanup" in notices[-1]
    assert log.count("session confirmation failed: OSError") == 1
    readable = True
    clock.now += CFG.poll_seconds
    if not session_exists:
        runner.halt.check = lambda: [(orch.FAIL, "preflight")]
        assert orch.round_once(runner.cfg, data, log.append, runner, dry=False, state_file=state)
        assert runner.confirm_launch is None and not (runner.tries or writes.done)
        assert "Inspect the launch workspace" not in json.loads(state.read_text())["halted"]
        return
    fake.replies["ls"] = ('[{"id":"agent","shortId":"agent","name":"ABC-7 ·","provider":"pi","thinking":"high",'
                          '"status":"running","cwd":"/clone","created":"1 second ago","background":false,'
                          '"labels":{"orchestrator":"ABC-7"},"workspaceId":"new"}]')
    clock.now += CFG.poll_seconds
    assert orch.round_once(runner.cfg, data, log.append, runner, dry=False, state_file=state)
    assert not runner.confirm_launch and runner.tries == {"ABC-7": 1}
    assert not runner.halt.cause and "halted" not in json.loads(state.read_text())
    assert len(notices) == 3 and notices[-1] is None
    assert any(line.endswith("host confirmed the session") for line in log)
    assert [row[:2] for row in writes.done] == [("comment", "ABC-7")]
    assert len([call for call in fake.calls if call[:2] == ["paseo", "run"]]) == 1
    assert all(call[:3] != ["paseo", "workspace", "archive"] for call in fake.calls)


def test_a_start_with_no_readable_id_is_alive_while_an_agent_of_its_title_runs() -> None:
    run = FakePaseo()
    host = orch.PaseoHost(run)
    run.replies["run"] = ""
    label = host.start("ABC-7 ·", "paseo run --quiet x", "/clone")
    assert label == "ABC-7 ·"
    assert host.alive(label)  # agent 1 runs under that title
    assert not host.alive("ABC-8 ·")  # idle
    assert not host.alive("ABC-5 ·")  # no agent has this title


def test_the_paseo_host_reads_an_agent_as_alive_while_it_runs() -> None:
    run = FakePaseo()
    host = orch.PaseoHost(run)
    assert host.alive("a1b2c3d4-0001") and host.alive("a1b2c3")  # the id that `run --quiet` printed, or the short one
    assert not host.alive("a1b2c3d4-0002")  # idle: its turn ended
    assert not host.alive("a1b2c3d4-0003")  # error
    assert not host.alive("gone")
    assert run.calls[0] == ["paseo", "ls", "-g", "--json"]
    assert not orch.PaseoHost(FakePaseo(ls=1)).alive("a1b2c3d4-0001")  # no list: not alive, as a closed herdr pane
    for reply in ('{"agents": []}', "not json"):  # a shape that is no list: not alive either
        run.replies["ls"] = reply
        assert not host.alive("a1b2c3d4-0001")


def test_the_paseo_host_tails_an_agent() -> None:
    run = FakePaseo()
    assert orch.PaseoHost(run).tail("a1b2c3d4-0001") == "entry 1\nentry 2\n"
    assert run.calls == [["paseo", "logs", "a1b2c3d4-0001", "--tail", "60"]]
    assert orch.PaseoHost(FakePaseo(logs=1)).tail("a1b2c3d4-0001") == "(paseo could not read the agent: daemon down)"


def test_the_paseo_host_lists_its_agents_in_herdrs_shape_and_lists_a_closed_one_as_done() -> None:
    spaces = orch.PaseoHost(FakePaseo()).workspaces()
    assert spaces == [
        {"workspace_id": "a1b2c3d4-0001", "label": "ABC-7 ·", "agent_status": "working"},
        {"workspace_id": "a1b2c3d4-0002", "label": "ABC-8 ·", "agent_status": "idle"},
        {"workspace_id": "a1b2c3d4-0003", "label": "ABC-9 ·", "agent_status": "done"},
        {"workspace_id": "a1b2c3d4-0004", "label": "ABC-6 ·", "agent_status": "done"},  # closed: its process ended
        {"workspace_id": "a1b2c3d4-0005", "label": "-", "agent_status": "working"}]
    # what the round and the check read from it: the keys of the finished agents, and a run that works too long
    assert [k for s in spaces if (k := orch.own_key(s["label"])) and s["agent_status"] in orch.FINISHED] == ["ABC-8", "ABC-9", "ABC-6"]
    assert not orch.PaseoHost(FakePaseo()).alive("a1b2c3d4-0004")
    assert orch.check_sessions(spaces, NOW, lambda key: NOW - 26 * HOUR) == [("WARN", "sessions: ABC-7 still works, 26 hours after its start")]
    with pytest.raises(RuntimeError, match="daemon down"):
        orch.PaseoHost(FakePaseo(ls=1)).workspaces()
    run = FakePaseo()
    run.replies["ls"] = '[{"id": "a9", "name": "ABC-9 ·", "status": "starting"}]'
    assert orch.PaseoHost(run).workspaces()[0]["agent_status"] == "unknown"  # a state it does not know stays open
    run.replies["ls"] = '[{"id": "a9", "name": "ABC-9 ·", "status": "initializing"}]'
    assert orch.PaseoHost(run).workspaces()[0]["agent_status"] == "working"  # a starting agent is busy


def test_the_paseo_host_archives_the_workspace_named_after_the_key_of_a_closed_agent() -> None:
    run = FakePaseo()
    orch.PaseoHost(run).close("a1b2c3d4-0002")  # abc-8: idle
    assert ["paseo", "archive", "a1b2c3d4-0002"] in run.calls
    assert [c for c in run.calls if c[:3] == ["paseo", "workspace", "archive"]] == [["paseo", "workspace", "archive", "wks_1"]]
    # another agent of the same key still runs: the workspace stays
    run = FakePaseo()
    run.replies["ls"] = PASEO_AGENTS.replace("abc-8-start", "abc-7-start")  # abc-7's first agent is `running`
    run.replies["workspace-ls"] = PASEO_WORKSPACES.replace("ABC-8", "ABC-7")
    orch.PaseoHost(run).close("a1b2c3d4-0002")
    assert not [c for c in run.calls if c[:3] == ["paseo", "workspace", "archive"]]
    # no workspace of that key: only the agent is archived
    run = FakePaseo()
    run.replies["workspace-ls"] = '[{"workspaceId": "wks_3", "name": "main"}]'
    orch.PaseoHost(run).close("a1b2c3d4-0002")
    assert not [c for c in run.calls if c[:3] == ["paseo", "workspace", "archive"]]
    with pytest.raises(RuntimeError, match="daemon down"):
        orch.PaseoHost(FakePaseo(**{"workspace-archive": 1})).close("a1b2c3d4-0002")
    with pytest.raises(RuntimeError, match="daemon down"):
        orch.PaseoHost(FakePaseo(**{"workspace-ls": 1})).close("a1b2c3d4-0002")


def test_the_paseo_host_lists_only_a_workspace_that_it_failed_to_archive() -> None:
    run = FakePaseo()
    run.replies["ls"] = "[]"  # no agent is left, and the workspaces ABC-8 and ABC-7 are the owner's: none is listed
    host = orch.PaseoHost(run)
    assert host.workspaces() == []
    assert not [c for c in run.calls if c[1] == "workspace"]  # nothing to retry: no read of the workspaces
    host.retry = {"ABC-8", "ABC-5"}  # ABC-5 has no workspace any more: it leaves the set
    assert host.workspaces() == [{"workspace_id": "workspace:ABC-8:wks_1", "label": "ABC-8", "key": "ABC-8", "agent_status": "done"}]
    assert host.retry == {"ABC-8"}
    assert [orch.space_key(s) for s in host.workspaces()] == ["ABC-8"]
    run.replies["ls"] = PASEO_AGENTS  # an agent of that key is back: its own close archives the workspace
    assert all(not s["workspace_id"].startswith("workspace:") for s in host.workspaces())
    run = FakePaseo()
    host = orch.PaseoHost(run)
    host.retry = {"ABC-8"}
    host.close("workspace:ABC-8:wks_1")  # only the workspace: no agent to archive
    assert run.calls == [["paseo", "workspace", "archive", "wks_1"]] and host.retry == set()
    with pytest.raises(RuntimeError, match="refused"):
        orch.PaseoHost(run).close("workspace:ABC-8:--force")
    with pytest.raises(RuntimeError, match="daemon down"):
        orch.PaseoHost(FakePaseo(**{"workspace-archive": 1})).close("workspace:ABC-8:wks_1")
    # a failed workspace list leaves the agents' list as it is, so the agents still close
    failing = orch.PaseoHost(FakePaseo(**{"workspace-ls": 1}))
    failing.retry = {"ABC-8"}
    assert len(failing.workspaces()) == 5


def test_a_failed_workspace_step_is_remembered_and_the_owners_workspace_is_never_archived() -> None:
    for broken in ("workspace-archive", "workspace-ls"):
        run = FakePaseo(**{broken: 1})
        host = orch.PaseoHost(run)
        with pytest.raises(RuntimeError, match="daemon down"):
            host.close("a1b2c3d4-0002")  # abc-8: the agent goes, the workspace step fails
        assert host.retry == {"ABC-8"}
    run = FakePaseo()
    run.replies["ls"] = "[]"
    run.codes.clear()
    host.run = run
    assert [s["workspace_id"] for s in host.workspaces()] == ["workspace:ABC-8:wks_1"]  # not ABC-7's: it never failed
    host.close("workspace:ABC-8:wks_1")
    assert host.retry == set() and host.workspaces() == []
    # the owner's workspace titled with a key that has been Done for a day, and no agent of the orchestrator ever had: not listed
    assert orch.PaseoHost(run).workspaces() == []


def test_a_workspace_whose_archive_failed_once_is_archived_by_a_later_round(tmp_path: Path) -> None:
    run = FakePaseo(**{"workspace-archive": 1})
    clock = Clock()
    clock.now = NOW
    writes = FakeWrites()
    runner = orch.Runner(fix_cfg(tmp_path), orch.PaseoHost(run), writes.writes(), clock)
    final = FinalReads({"ABC-8": ("Done", NOW - 13 * HOUR)})
    run.replies["workspace-ls"] = '[{"workspaceId": "wks_1", "name": "ABC-8"}, {"workspaceId": "wks_2", "name": "ABC-7"}]'
    run.replies["ls"] = ('[{"id": "a1b2c3d4-0002", "shortId": "a1b2c4", "name": "ABC-8 ·",'
                         ' "status": "idle"}]')
    _, first = runner.plan([], [], False, final=final)
    assert ("paseo", "archive", "a1b2c3d4-0002") in {tuple(c[:3]) for c in run.calls}  # the agent went; the workspace did not
    assert [e.kind for e in first] == ["close_failed"]
    run.replies["ls"] = "[]"  # the archived agent has left the list: only the failed workspace shows now, never ABC-7's
    run.calls.clear()
    clock.now = NOW + 600  # the failed step has its own entry: its first retry is the next round, and it fails again
    _, again = runner.plan([], [], False, final=final)
    assert [c for c in run.calls if c[:3] == ["paseo", "workspace", "archive"]] == [["paseo", "workspace", "archive", "wks_1"]]
    assert [e.kind for e in again] == ["close_failed"]
    run.calls.clear()
    clock.now = NOW + 1200  # then it waits an hour like any failed close
    _, waiting = runner.plan([], [], False, final=final)
    assert waiting == [] and not [c for c in run.calls if c[:3] == ["paseo", "workspace", "archive"]]
    run.codes.clear()
    clock.now = NOW + 600 + HOUR
    _, later = runner.plan([], [], False, final=final)
    assert [c for c in run.calls if c[:3] == ["paseo", "workspace", "archive"]] == [["paseo", "workspace", "archive", "wks_1"]]
    assert [e.kind for e in later] == ["closed"]


def test_the_paseo_host_closes_an_agent_by_archiving_it() -> None:
    run = FakePaseo()
    orch.PaseoHost(run).close("a1b2c3d4-0002")
    assert run.calls == [["paseo", "ls", "-g", "--json"], ["paseo", "archive", "a1b2c3d4-0002"],
                         ["paseo", "workspace", "ls", "--json"], ["paseo", "workspace", "archive", "wks_1"]]
    with pytest.raises(RuntimeError, match="daemon down"):
        orch.PaseoHost(FakePaseo(archive=1)).close("a1b2c3d4-0002")
    with pytest.raises(RuntimeError, match="refused"):
        orch.PaseoHost(run).close("--force")


def test_the_config_takes_the_paseo_host_and_makes_it(tmp_path: Path) -> None:
    cfg = orch.load_config(config_with(tmp_path, session_host="session_host = 'paseo'"))
    assert cfg.session_host == "paseo" and isinstance(orch.make_host(cfg), orch.PaseoHost)
    assert isinstance(orch.make_host(CFG), orch.HerdrHost)


def test_check_reads_the_sessions_of_paseo_too(tmp_path: Path) -> None:
    cfg = orch.load_config(config_with(tmp_path, session_host="session_host = 'paseo'"))
    sessions = [{"label": "ABC-12 ·", "agent_status": "working"}]
    said: list[str] = []
    live = dataclasses.replace(probes(sessions=sessions, started=lambda key: NOW - DAY - 1), paseo_profile=lambda: ("claude",))
    assert orch.run_check(cfg, live, said.append) == 0
    assert "WARN sessions: ABC-12 still works, 24 hours after its start" in said

    def boom() -> Any:
        raise RuntimeError("paseo ls failed")

    said = []
    orch.run_check(cfg, orch.Probes(environ={"LINEAR_API_KEY": "x"}, linear=lambda: None, github=lambda: None, log=lambda: FRESH_LOG,
                                    sessions=boom, installed=lambda: (INSTALLED, []), state=lambda: FRESH_STATE, run_started=lambda key: None,
                                    clock=lambda: NOW), said.append)
    assert "WARN sessions: paseo gave no list (RuntimeError)" in said
    process = orch.load_config(config_with(tmp_path, session_host="session_host = 'process'"))
    said = []
    orch.run_check(process, probes(sessions=sessions), said.append)
    assert not any(line.startswith(("ok   sessions", "WARN sessions")) for line in said)  # a process has no session list


class FinalReads:
    """`Reads.final` with a count of the reads: key -> (state, since) or (state, since, labels); no labels: ()."""

    def __init__(self, states: dict[str, tuple[Any, ...]]) -> None:
        self.states = states
        self.calls: list[str] = []

    def __call__(self, key: str) -> tuple[str, float | None, tuple[str, ...]]:
        self.calls.append(key)
        found = self.states[key]
        if isinstance(found, Exception):
            raise found
        return (*found, ()) if len(found) == 2 else found  # type: ignore[return-value]  # the test's own shapes


def test_a_round_closes_what_to_close_returns_one_line_each_and_a_failed_close_stops_nothing(tmp_path: Path) -> None:
    runner, host, _, clock = runner_of(tmp_path)
    clock.now = NOW
    host.spaces = [space(workspace="w7"), space("ABC-8 ·", workspace="w8"),
                   space("ABC-9 ·", workspace="w9"), space("ABC-2 ·", workspace="w2"),
                   space("agents", workspace="w1")]
    host.refuse = {"w8"}
    final = FinalReads({"ABC-7": ("Done", NOW - 13 * HOUR), "ABC-8": ("Canceled", NOW - 20 * HOUR), "ABC-9": ("Done", NOW - 2 * HOUR)})
    issues = [issue("ABC-2", state="In Progress", area="backend")]  # read by the round: open, so no read and no close
    plan, events = runner.plan(issues, [], False, final=final)
    assert host.closed == ["w7"]
    assert [e.text for e in events] == ["closed the workspace of ABC-7 (Done 13 h ago)", "close of the workspace of ABC-8 failed: RuntimeError"]
    assert plan["events"] == [{"event": "closed", "key": "ABC-7"}, {"event": "close_failed", "key": "ABC-8", "reason": "RuntimeError"}]
    assert sorted(final.calls) == ["ABC-7", "ABC-8", "ABC-9"]  # one read per workspace of ours, none for an open issue


def test_a_failed_close_or_read_waits_an_hour_and_a_repeated_error_prints_once(tmp_path: Path) -> None:
    runner, host, _, clock = runner_of(tmp_path)
    clock.now = NOW
    host.spaces = [space(workspace="w7"), space("ABC-8 ·", workspace="w8")]
    host.refuse = {"w7"}
    final = FinalReads({"ABC-7": ("Done", NOW - 13 * HOUR), "ABC-8": RuntimeError("gone")})  # type: ignore[dict-item]  # a failing read
    _, first = runner.plan([], [], False, final=final)
    assert [e.kind for e in first] == ["close_failed", "close_failed"]
    for step in (1, 2):  # inside the hour: no read, no close, no line
        clock.now = NOW + step * 600
        _, again = runner.plan([], [], False, final=final)
        assert again == [] and final.calls == ["ABC-7", "ABC-8"]
    clock.now = NOW + HOUR  # an hour on: both are tried again; w7's same error prints no line, ABC-8's changed error prints one
    final.states["ABC-8"] = ValueError("changed")  # type: ignore[assignment]  # a failing read
    _, later = runner.plan([], [], False, final=final)
    assert [(e.kind, e.key, e.text.rsplit(" ", 1)[-1]) for e in later] == [("close_failed", "ABC-8", "ValueError")]
    assert final.calls == ["ABC-7", "ABC-8", "ABC-8"]
    assert runner.backing_off("close", "w7") and runner.backing_off("read", "ABC-8")
    host.refuse = set()  # a close that works clears the failure
    clock.now = NOW + 2 * HOUR
    _, done = runner.plan([], [], False, final=final)
    assert [e.kind for e in done] == ["closed"] and not runner.backing_off("close", "w7")


def test_a_final_state_is_read_once_and_a_reopened_issue_is_read_again(tmp_path: Path) -> None:
    runner, host, _, clock = runner_of(tmp_path)
    clock.now = NOW
    host.spaces = [space()]
    final = FinalReads({"ABC-7": ("Done", NOW - 2 * HOUR)})
    for _ in range(3):
        runner.plan([], [], False, final=final)
    assert final.calls == ["ABC-7"] and host.closed == []
    clock.now = NOW + 10 * HOUR  # 12 hours after Done: the cached time closes it with no new read
    runner.plan([], [], False, final=final)
    assert final.calls == ["ABC-7"] and host.closed == ["w7"]
    runner.plan([issue("ABC-7", state="In Progress")], [], False, final=final)  # reopened: the cache goes
    assert "ABC-7" not in runner.finals


def test_a_state_that_is_not_final_is_read_again_after_an_hour(tmp_path: Path) -> None:
    runner, host, _, clock = runner_of(tmp_path)
    clock.now = NOW
    host.spaces = [space()]
    final = FinalReads({"ABC-7": ("Backlog", None)})
    runner.plan([], [], False, final=final)
    clock.now = NOW + HOUR - 1
    runner.plan([], [], False, final=final)
    assert final.calls == ["ABC-7"]
    clock.now = NOW + HOUR
    runner.plan([], [], False, final=final)
    assert final.calls == ["ABC-7", "ABC-7"]


def test_a_failed_read_or_list_gives_its_error_type_and_the_round_goes_on(tmp_path: Path) -> None:
    runner, host, _, clock = runner_of(tmp_path)
    clock.now = NOW
    host.spaces = [space(), space("ABC-8 ·", workspace="w8")]
    final = FinalReads({"ABC-7": RuntimeError("Linear is down"), "ABC-8": ("Done", NOW - 13 * HOUR)})  # type: ignore[dict-item]
    _, events = runner.plan([issue("ABC-3", area="docs")], [], False, final=final)
    assert [e.text for e in events] == ["started ABC-3 (claude-sonnet-5-5, medium)", "close of the workspace of ABC-7: reading the issue failed: RuntimeError",
                                        "closed the workspace of ABC-8 (Done 13 h ago)"]

    class NoList(FakeHost):
        def workspaces(self) -> list[dict[str, Any]]:
            raise RuntimeError("herdr workspace list failed")

    other = orch.Runner(fix_cfg(tmp_path), NoList(), FakeWrites().writes(), clock)
    _, events = other.plan([issue("ABC-3", area="docs")], [], False, final=final)
    # The start reads the list first and starts nothing without it; the close gives its own event.
    assert [e.text for e in events] == ["no start this round: the session list failed: RuntimeError", "closing workspaces: the list failed: RuntimeError"]


def test_a_dry_run_says_would_close_once_and_closes_nothing(tmp_path: Path) -> None:
    runner, host, _, clock = runner_of(tmp_path)
    clock.now = NOW
    host.spaces = [space()]
    final = FinalReads({"ABC-7": ("Done", NOW - 13 * HOUR)})
    _, events = runner.plan([], [], True, final=final)
    assert [e.text for e in events] == ["would close the workspace of ABC-7 (Done 13 h ago)"]
    _, events = runner.plan([], [], True, final=final)
    assert events == [] and host.closed == []


def test_close_after_hours_0_reads_nothing_and_closes_nothing(tmp_path: Path) -> None:
    runner, host, _, clock = runner_of(tmp_path, close_after_hours=0)
    clock.now = NOW
    host.spaces = [space()]
    final = FinalReads({"ABC-7": ("Done", NOW - 99 * HOUR)})
    assert runner.plan([], [], False, final=final)[1] == []
    assert final.calls == [] and host.closed == []


def test_a_round_prints_the_close_line(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    runner, host, _, clock = runner_of(tmp_path)
    clock.now = NOW
    host.spaces = [space()]
    reads = orch.Reads(issues=lambda: [], prs=lambda: [], final=FinalReads({"ABC-7": ("Done", NOW - 13 * HOUR)}))
    assert orch.round_once(runner.cfg, reads, runner=runner, dry=False)
    out = capsys.readouterr().out
    assert "closed the workspace of ABC-7 (Done 13 h ago)" in out.splitlines()
    assert '"event": "closed", "key": "ABC-7"' in out


def test_read_final_gives_the_state_and_the_time_it_entered_a_closed_state(monkeypatch: pytest.MonkeyPatch) -> None:
    class Api:
        def __init__(self, state: str, entered: str | None) -> None:
            self.state, self.when, self.asked = state, entered, []

        def issue(self, key: str) -> dict[str, Any]:
            return {"identifier": key, "state": self.state, "labels": ["orchestrator"]}

        def entered(self, key: str, state: str) -> str | None:
            self.asked.append((key, state))
            return self.when

    done = Api("Done", "2026-11-10T00:00:00.000Z")
    monkeypatch.setattr(orch, "linear_api", lambda cfg: done)
    assert orch.read_final(CFG, "ABC-7") == ("Done", NOW - 12 * HOUR, ("orchestrator",))  # the labels: the same read
    assert done.asked == [("ABC-7", "Done")]
    monkeypatch.setattr(orch, "linear_api", lambda cfg: Api("Canceled", None))  # made in that state: no move led there
    assert orch.read_final(CFG, "ABC-7") == ("Canceled", None, ("orchestrator",))
    open_one = Api("In Progress", "2026-11-10T00:00:00.000Z")
    monkeypatch.setattr(orch, "linear_api", lambda cfg: open_one)
    assert orch.read_final(CFG, "ABC-7") == ("In Progress", None, ("orchestrator",))
    assert open_one.asked == []


# -- The skipped issues and the view of `--watch` ---------------------------------------------------------------

def test_an_issue_with_any_skip_label_is_a_hands_off_count_and_the_plan_has_no_session_line(tmp_path: Path) -> None:
    # the plan has no `session` row; a skip label's issues in Todo are one count whatever the label
    issues = [issue("ABC-651", labels=("workflow",)), issue("ABC-479", labels=("workflow",), size="L")]
    plan, events = plan_of(tmp_path, issues, [])
    assert events == [] and plan["areas"] == [] and "session" not in plan
    assert plan["hands_off"] == 2 and plan["other_waits"] == []
    assert "/implement" not in "\n".join(orch.plan_text(plan, []) + orch.render(plan, T0, 54, 40, False))


def test_the_hands_off_issues_in_todo_make_one_count(tmp_path: Path) -> None:
    issues = [issue(f"ABC-69{n}", labels=("hands-off",), priority=None) for n in range(3)]
    issues += [issue("ABC-688", labels=("hands-off", "workflow")),  # the owner's own work: hands-off wins
               issue("ABC-689", state="In Progress", labels=("hands-off",))]  # only an issue in Todo counts
    plan, _ = plan_of(tmp_path, issues, [])
    assert (plan["hands_off"], plan["other_waits"]) == (4, [])
    assert "yours  4 hands-off: /queue" in orch.plan_text(plan, [])
    assert orch.render(plan, T0, 54, 40, False)[-2:] == ["other", "  yours  4 hands-off: /queue"]
    three, _ = plan_of(tmp_path, issues[:3], [])
    assert orch.render(three, T0, 54, 40, False)[-1] == "  yours  3 hands-off: /queue"


def test_with_no_skipped_issue_the_plan_shows_no_hands_off_line(tmp_path: Path) -> None:
    plan, _ = plan_of(tmp_path, [issue("ABC-2", area="docs")], [])
    assert plan["hands_off"] == 0
    text = "\n".join(orch.plan_text(plan, []) + orch.render(plan, T0, 54, 40, False))
    assert "/implement" not in text and "hands-off" not in text and "other" not in text


def test_the_yours_row_of_the_text_keeps_the_count_and_the_closes_by_hand() -> None:
    plan = dict(VIEW_PLAN, hands_off=2, close_by_hand=[{"key": "ABC-7", "pr": 70, "label": ""}])
    assert any(line.endswith("yours         2 hands-off: /queue · ABC-7 canceled: close PR 70 by hand") for line in orch.plan_text(plan, []))
    assert orch.queue_lines(plan)[-1] == "yours         2 hands-off: /queue · ABC-7 canceled: close PR 70 by hand"


def test_each_holder_carries_the_kind_that_the_view_marks(tmp_path: Path) -> None:
    issues = [issue("ABC-9", state="In Review", area="infra"), issue("ABC-5", state="In Progress", area="backend"),
              issue("ABC-2", area="docs")]
    plan, _ = plan_of(tmp_path, issues, [pr(9, "abc-9-x")], max_runs=3)
    assert {q["area"]: q["holders"] for q in plan["areas"]} == {
        "backend": [{"key": "ABC-5", "state": "In Progress", "kind": "building"}],
        "infra": [{"key": "ABC-9", "state": "In Review", "kind": "review"}],
        "docs": [{"key": "ABC-2", "state": "starting", "kind": "building"}]}


def test_an_escalated_issue_holds_no_area_so_no_holder_is_marked_yours(tmp_path: Path) -> None:
    plan, _ = plan_of(tmp_path, [issue("ABC-4", state="Escalated", area="infra")], [pr(4, "abc-4-x")], max_runs=3)
    assert [h for q in plan["areas"] for h in q["holders"]] == [] and plan["yours"] == [{"key": "ABC-4", "pr": 4, "reason": "not clear: read the issue"}]
    assert "yours" not in orch.MARKS


# -- The pull request of a canceled issue closes ----------------------------------------------------------------


MINUTE = 60


def test_a_canceled_issue_with_an_open_pull_request_gets_one_close_with_one_comment(tmp_path: Path) -> None:
    runner, _, writes, clock = runner_of(tmp_path)
    clock.now = NOW
    final = FinalReads({"ABC-7": ("Canceled", NOW - 11 * MINUTE)})
    _, events = runner.plan([], [pr(70, "abc-7-thing")], False, final=final)
    assert writes.done == [("close", "70", "Closed: ABC-7 was canceled")]
    assert [e.text for e in events] == ["closed PR 70 (ABC-7 canceled 11 min ago)"]
    assert [(e.kind, e.key) for e in events] == [("closed_pr", "ABC-7")]


def test_a_canceled_issue_with_no_pull_request_closes_nothing_and_reads_nothing(tmp_path: Path) -> None:
    runner, _, writes, clock = runner_of(tmp_path)
    clock.now = NOW
    final = FinalReads({"ABC-7": ("Canceled", NOW - HOUR), "ABC-8": ("Backlog", None)})
    _, events = runner.plan([], [pr(80, "abc-8-other")], False, final=final)
    assert events == [] and writes.done == [] and final.calls == ["ABC-8"]  # ABC-7 has no pull request: never read


def test_a_merged_or_closed_pull_request_is_never_closed(tmp_path: Path) -> None:
    runner, _, writes, clock = runner_of(tmp_path)
    clock.now = NOW
    final = FinalReads({"ABC-7": ("Canceled", NOW - HOUR), "ABC-8": ("Canceled", NOW - HOUR)})
    merged, closed = pr(70, "abc-7-thing"), pr(80, "abc-8-thing")
    merged["state"], closed["state"] = "MERGED", "CLOSED"
    _, events = runner.plan([], [merged, closed], False, final=final)
    assert events == [] and writes.done == []


@pytest.mark.parametrize("state", ["Done", "Duplicate", "Backlog", "Triage"])
def test_the_pull_request_of_an_issue_in_another_state_stays_open(tmp_path: Path, state: str) -> None:
    runner, _, writes, clock = runner_of(tmp_path)
    clock.now = NOW
    final = FinalReads({"ABC-7": (state, NOW - HOUR)})
    _, events = runner.plan([], [pr(70, "abc-7-thing")], False, final=final)
    assert events == [] and writes.done == []


def test_the_pull_request_of_an_open_issue_is_not_read_and_stays_open(tmp_path: Path) -> None:
    runner, _, writes, clock = runner_of(tmp_path)
    clock.now = NOW
    final = FinalReads({})
    runner.plan([issue("ABC-7", state="In Review", area="docs")], [pr(70, "abc-7-thing")], False, final=final)
    assert final.calls == [] and writes.done == []


def test_a_cancel_inside_the_grace_time_waits_and_closes_once_it_passed(tmp_path: Path) -> None:
    runner, _, writes, clock = runner_of(tmp_path)
    clock.now = NOW
    final = FinalReads({"ABC-7": ("Canceled", NOW - 9 * MINUTE)})
    assert runner.plan([], [pr(70, "abc-7-thing")], False, final=final)[1] == []
    assert writes.done == []
    clock.now = NOW + MINUTE  # 10 minutes after the cancel: the cached time passes the grace, a fresh read confirms it
    runner.plan([], [pr(70, "abc-7-thing")], False, final=final)
    assert writes.done == [("close", "70", "Closed: ABC-7 was canceled")]
    assert final.calls == ["ABC-7", "ABC-7"]


def test_a_cancel_with_no_time_keeps_its_pull_request(tmp_path: Path) -> None:
    runner, _, writes, clock = runner_of(tmp_path)
    clock.now = NOW
    runner.plan([], [pr(70, "abc-7-thing")], False, final=FinalReads({"ABC-7": ("Canceled", None)}))
    assert writes.done == []


def test_a_cancel_undone_after_the_cached_read_keeps_its_pull_request(tmp_path: Path) -> None:
    runner, _, writes, clock = runner_of(tmp_path)
    clock.now = NOW
    final = FinalReads({"ABC-7": ("Canceled", NOW - 5 * MINUTE)})
    runner.plan([], [pr(70, "abc-7-thing")], False, final=final)
    final.states["ABC-7"] = ("Backlog", None)  # the owner moved it out of Canceled; the cache still says Canceled
    clock.now = NOW + 6 * MINUTE
    assert runner.plan([], [pr(70, "abc-7-thing")], False, final=final)[1] == []
    assert writes.done == [] and runner.finals["ABC-7"][0] == "Backlog"


def test_a_cancel_grace_of_0_closes_at_once() -> None:
    assert orch.to_drop([pr(70, "abc-7-thing")], {"ABC-7": {"state": "Canceled", "since": NOW}}, NOW, "Canceled", 0, "ABC") == [
        orch.Dropped(70, "ABC-7", 0)]


def test_a_branch_with_no_key_of_the_team_is_neither_read_nor_closed(tmp_path: Path) -> None:
    runner, _, writes, clock = runner_of(tmp_path)
    clock.now = NOW
    final = FinalReads({"OTHER-7": ("Canceled", NOW - HOUR)})
    _, events = runner.plan([], [pr(70, "bump-node-20"), pr(71, "other-7-thing")], False, final=final)
    assert events == [] and final.calls == [] and writes.done == []
    assert orch.to_drop([pr(71, "other-7-thing")], {"OTHER-7": {"state": "Canceled", "since": NOW - HOUR}}, NOW, "Canceled",
                        10, "ABC") == []


def test_a_dry_run_says_would_close_the_pull_request_once_and_closes_nothing(tmp_path: Path) -> None:
    runner, _, writes, clock = runner_of(tmp_path)
    clock.now = NOW
    final = FinalReads({"ABC-7": ("Canceled", NOW - HOUR)})
    _, events = runner.plan([], [pr(70, "abc-7-thing")], True, final=final)
    assert [e.text for e in events] == ["would close PR 70 (ABC-7 canceled)"]
    assert runner.plan([], [pr(70, "abc-7-thing")], True, final=final)[1] == []
    assert writes.done == []


def test_a_failed_close_or_read_gives_its_error_type_and_the_round_goes_on(tmp_path: Path) -> None:
    runner, _, writes, clock = runner_of(tmp_path)
    clock.now = NOW
    writes.fail = True
    final = FinalReads({"ABC-7": ("Canceled", NOW - HOUR), "ABC-8": RuntimeError("Linear is down")})  # type: ignore[dict-item]
    _, events = runner.plan([], [pr(70, "abc-7-thing"), pr(80, "abc-8-thing")], False, final=final)
    assert [e.text for e in events] == ["close of the pull request of ABC-8: reading the issue failed: RuntimeError",
                                        "close of PR 70 failed: RuntimeError"]
    assert {e.kind for e in events} == {"close_pr_failed"}


def test_main_with_dry_run_prints_would_close_and_closes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = tmp_path / "orchestrator.toml"
    config.write_text(TEST_TOML)
    writes = FakeWrites()
    reads = orch.Reads(issues=lambda: [], prs=lambda: [pr(70, "abc-7-thing")],
                       final=FinalReads({"ABC-7": ("Canceled", datetime.now().timestamp() - HOUR)}))
    assert orch.main(["--config", str(config), "--once", "--dry-run"], reads=reads, host=FakeHost(), writes=writes.writes()) == 0
    assert "would close PR 70 (ABC-7 canceled)" in capsys.readouterr().out.splitlines()
    assert writes.done == []


def test_the_real_close_runs_gh_pr_close_with_the_comment_and_deletes_no_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []

    class Done:
        returncode, stdout, stderr = 0, "", ""

    monkeypatch.setattr(orch, "run_quiet", lambda argv: seen.append(list(argv)) or Done())
    close = orch.real_writes(CFG).close
    assert close is not None
    close(70, "Closed: ABC-7 was canceled")
    assert seen == [["gh", "pr", "close", "70", "--repo", "acme/product", "--comment", "Closed: ABC-7 was canceled"]]


def test_the_example_config_sets_the_canceled_state_and_the_grace_time() -> None:
    assert (CFG.canceled, CFG.cancel_grace_minutes) == ("Canceled", 10)


VIEW_PLAN = {"version": 2, "time": "2026-11-10T18:30:00Z", "poll_seconds": 60, "building": 1, "max_runs": 3, "reviewing": 1,
             "yours": 3, "slots": [], "events": [], "stacks": [],
             "areas": [{"area": "orchestrator", "holders": [{"key": "ABC-677", "state": "In Review", "kind": "review"}],
                        "queue": [{"key": k, "mark": None} for k in ("ABC-684", "ABC-665", "ABC-672", "ABC-674", "ABC-388", "ABC-658")]},
                       {"area": "docs", "holders": [{"key": "ABC-647", "state": "In Progress", "kind": "building"}], "queue": []}],
             "other_waits": [{"key": "ABC-689", "reason": "no Area label: add one"}], "hands_off": 0}
VIEW_NOW = datetime.fromisoformat("2026-11-10T18:30:12+00:00").timestamp()
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def view(width: int = 54, height: int = 40, color: bool = False, now: float = VIEW_NOW, plan: Any = None) -> list[str]:
    return list(orch.render(plan or VIEW_PLAN, now, width, height, color, tz=orch.UTC))


def test_the_view_wraps_the_queue_to_the_width_and_never_inside_a_key() -> None:
    assert view(54) == [
        "18:30 · build 1/3 · review 1 · you 3 · 12 s ago",  # "updated" does not fit in 54 columns
        "orchestrator", "◐ ABC-677", "  ▸ ABC-684, ABC-665, ABC-672, ABC-674, ABC-388,", "    ABC-658",
        "docs", "● ABC-647", "other", "  ABC-689 no Area label: add one"]
    assert view(100)[:4] == ["18:30 · build 1/3 · review 1 · you 3 · updated 12 s ago", "orchestrator", "◐ ABC-677",
                             "  ▸ ABC-684, ABC-665, ABC-672, ABC-674, ABC-388, ABC-658"]
    assert all(len(line) <= 54 for line in view(54))


def test_the_view_colors_the_marks_the_counts_and_the_age_and_reads_the_same_without_color() -> None:
    lines = view(54, color=True)
    assert [ANSI.sub("", line) for line in lines] == view(54)
    assert "\x1b[1;33myou 3\x1b[0m" in lines[0] and "\x1b[90m12 s ago\x1b[0m" in lines[0]
    assert "\x1b[34m◐ ABC-677\x1b[0m" in lines and "\x1b[32m● ABC-647\x1b[0m" in lines and "\x1b[1morchestrator\x1b[0m" in lines
    assert lines[3].startswith("\x1b[90m  ▸ ABC-684")
    assert "\x1b[31m3 min ago\x1b[0m" in view(54, color=True, now=VIEW_NOW + 169)[0]  # older than three poll periods: red
    assert "you 0 ·" in view(54, color=True, plan=dict(VIEW_PLAN, yours=0))[0]  # bold yellow only when something waits on you
    failed = view(54, color=True, plan=dict(VIEW_PLAN, failed={"time": "2026-11-10T18:31:00Z", "error": "OSError"}))
    assert failed[0] == "\x1b[31m18:31 · round failed: OSError\x1b[0m" and "build 1/3" in failed[1]


def test_the_view_shortens_each_queue_to_one_line_when_the_plan_is_taller_than_the_pane() -> None:
    assert view(54, height=8) == [
        "18:30 · build 1/3 · review 1 · you 3 · 12 s ago", "orchestrator", "◐ ABC-677", "  ▸ ABC-684, ABC-665, ABC-672, ABC-674 +2 more",
        "docs", "● ABC-647", "other", "  ABC-689 no Area label: add one"]
    assert view(54, height=6) == view(54, height=8)[:6]  # the top line and the holders always show; `other` goes first
    many = dict(VIEW_PLAN, other_waits=[{"key": f"ABC-{n}", "reason": "no Area label: add one"} for n in range(5)])
    assert view(54, height=9, plan=many)[-3:] == ["other", "  ABC-0 no Area label: add one", "  +4 more"]
    assert view(54, height=5) == ["18:30 · build 1/3 · review 1 · you 3 · 12 s ago", "orchestrator", "◐ ABC-677", "docs", "● ABC-647"]
    assert view(54, height=3) == ["18:30 · build 1/3 · review 1 · you 3 · 12 s ago", "orchestrator", "◐ ABC-677"]  # never taller


def test_the_view_of_a_plan_with_no_round_yet_shows_the_failure_or_says_so() -> None:
    empty = {"version": 1, "time": None, "poll_seconds": 60}
    assert view(plan=empty) == ["no round yet"]
    assert view(plan=dict(empty, failed={"time": "2026-11-10T18:31:00Z", "error": "OSError"})) == ["18:31 · round failed: OSError"]
    assert view(now=VIEW_NOW + 2 * 3600)[0].endswith("updated 2 h ago")  # an age of an hour or more counts in hours


def test_watch_draws_the_state_file_when_it_changes_and_reads_nothing_else(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    drawn: list[str] = []
    looks = 0

    def sleep(_seconds: float) -> None:
        nonlocal looks
        looks += 1
        if looks == 2:
            orch.write_state(path, dict(VIEW_PLAN, yours=0))
            orch.os.utime(path, ns=(1, 1))  # a new mtime, whatever the file system's resolution
        if looks == 4:
            raise KeyboardInterrupt

    orch.write_state(path, VIEW_PLAN)
    assert orch.watch(path, drawn.append, lambda: (54, 40), False, sleep, lambda: VIEW_NOW) == 0
    screens = [d for d in drawn if d.startswith("\x1b[H\x1b[2J")]
    assert len(screens) == 2  # the first look, then the change: the same screen is not drawn again
    assert "you 3" in screens[0] and "you 0" in screens[1]
    path.unlink()
    drawn.clear()
    assert orch.watch(path, drawn.append, lambda: (54, 40), False, lambda s: (_ for _ in ()).throw(KeyboardInterrupt)) == 0
    assert drawn[0].endswith(f"no plan yet in {path}")


def test_main_watch_draws_with_no_color_when_the_output_is_no_terminal(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = tmp_path / "c.toml"
    state = tmp_path / "state.json"
    config.write_text(TEST_TOML.replace(
        'state_file = "~/.local/state/orchestrator/state.json"', f'state_file = "{state}"'))
    orch.write_state(state, dict(VIEW_PLAN, yours=2))

    def stop(_seconds: float) -> None:
        raise KeyboardInterrupt

    assert orch.main(["--config", str(config), "--watch"], reads_of(RuntimeError("no read"), []), stop) == 0
    out = capsys.readouterr().out
    assert "build 1/3 · review 1 · you 2" in out and "\x1b[1;33m" not in out


@pytest.mark.parametrize("label", ["hands-off", "workflow"])
def test_the_owners_canceled_issue_keeps_its_pull_request_and_shows_in_the_row_yours(tmp_path: Path, label: str) -> None:
    runner, _, writes, clock = runner_of(tmp_path)
    clock.now = NOW
    final = FinalReads({"ABC-7": ("Canceled", NOW - HOUR, (label, "docs"))})
    plan, events = runner.plan([], [pr(70, "abc-7-thing")], False, final=final)
    assert writes.done == [] and events == []
    assert plan["close_by_hand"] == [{"key": "ABC-7", "pr": 70, "label": label}]
    assert "yours  ABC-7 canceled: close PR 70 by hand" in orch.plan_text(plan, events)
    assert "yours  ABC-7 canceled: close PR 70 by hand" in orch.queue_lines(plan)
    assert orch.render(plan, NOW, 80, 40, False)[-1] == "  ABC-7 canceled: close PR 70 by hand"
    runner.plan([], [pr(70, "abc-7-thing")], False, final=final)  # the next round: the cached labels, no new read
    assert final.calls == ["ABC-7"]
    assert runner.plan([], [], False, final=final)[0]["close_by_hand"] == []  # closed by the owner: the row goes


def test_the_row_yours_goes_when_the_owner_undoes_the_cancel_and_an_hour_passed(tmp_path: Path) -> None:
    runner, _, writes, clock = runner_of(tmp_path)
    clock.now = NOW
    final = FinalReads({"ABC-7": ("Canceled", NOW - HOUR, ("hands-off",))})
    assert runner.plan([], [pr(70, "abc-7-thing")], False, final=final)[0]["close_by_hand"]
    final.states["ABC-7"] = ("Backlog", None, ("hands-off",))  # undone: a state the round does not read
    clock.now = NOW + 30 * MINUTE
    assert runner.plan([], [pr(70, "abc-7-thing")], False, final=final)[0]["close_by_hand"]  # cached until the hour
    clock.now = NOW + HOUR + MINUTE
    plan, _ = runner.plan([], [pr(70, "abc-7-thing")], False, final=final)
    assert plan["close_by_hand"] == [] and writes.done == []


def test_a_skip_label_that_only_the_fresh_read_shows_keeps_the_pull_request_too(tmp_path: Path) -> None:
    runner, _, writes, clock = runner_of(tmp_path)
    clock.now = NOW
    final = FinalReads({"ABC-7": ("Canceled", NOW - 5 * MINUTE)})
    runner.plan([], [pr(70, "abc-7-thing")], False, final=final)
    final.states["ABC-7"] = ("Canceled", NOW - 5 * MINUTE, ("hands-off",))  # the owner added the label after the cached read
    clock.now = NOW + 6 * MINUTE
    plan, _ = runner.plan([], [pr(70, "abc-7-thing")], False, final=final)
    assert writes.done == [] and plan["close_by_hand"] == [{"key": "ABC-7", "pr": 70, "label": "hands-off"}]


def test_the_row_yours_is_part_of_what_the_loop_prints_when_it_changes() -> None:
    plan = dict(VIEW_PLAN, areas=[], close_by_hand=[])
    assert orch.shown_state(plan) != orch.shown_state(dict(plan, close_by_hand=[{"key": "ABC-7", "pr": 70, "label": "hands-off"}]))


def test_a_change_of_the_hands_off_count_prints_the_plan_again() -> None:
    plan = dict(VIEW_PLAN, close_by_hand=[])
    assert orch.shown_state(dict(plan, hands_off=3)) != orch.shown_state(dict(plan, hands_off=4))


def test_the_real_probe_reads_a_run_start_from_the_issues_comments(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[tuple[str, list[str], list[str]]] = []

    class FakeLinear:
        def issues(self, team: str, states: list[str], keys: list[str]) -> list[dict[str, Any]]:
            asked.append((team, states, keys))
            comments = [{"created": "2026-11-10T11:00:00Z", "body": "Run: claude-sonnet-5-5, effort medium"}] if keys == ["ABC-7"] else []
            return [{"identifier": keys[0], "comments": comments}]

    monkeypatch.setattr(orch, "linear_api", lambda cfg, environ=None: FakeLinear())
    probe = orch.real_probes(CFG).run_started
    assert probe("ABC-7") == NOW - HOUR and probe("ABC-8") is None
    assert asked == [(CFG.team, [], ["ABC-7"]), (CFG.team, [], ["ABC-8"])]


# -- the orchestrator wakes the session that opened a pull request -----------------------------------------------

WAKE_TEXT = orch.wake_text(7, "changes requested")


def paseo_agents(*agents: tuple[str, str, str, str]) -> str:
    """The list of `paseo ls -g --json` for (id, name, status, created) each."""
    return "[" + ",".join(f'{{"id": "{i}", "shortId": "{i[:6]}", "name": "{n}", "status": "{s}", "created": "{c}"}}' for i, n, s, c in agents) + "]"


def paseo_send_calls(fake: FakePaseo) -> list[list[str]]:
    return [c for c in fake.calls if c[1] == "send"]


def test_the_wake_message_names_the_pull_request_the_cause_and_the_step_in_one_line() -> None:
    assert "\n" not in WAKE_TEXT
    for why in ("changes requested", "failed", "conflict with main"):
        text = orch.wake_text(7, why)
        assert "PR #7" in text and why in text and 'agents/implementer.md "When a review asks for changes"' in text and "gh pr ready 7 --undo" in text


@pytest.mark.parametrize("status", ["idle", "closed", "error"])
def test_the_paseo_host_wakes_an_agent_that_is_idle_closed_or_in_error_through_a_file(status: str) -> None:
    fake = FakePaseo()
    fake.replies["ls"] = paseo_agents(("a1b2c3d4-0001", "ABC-7 ·", status, "2026-11-09T22:00:00Z"))
    seen: list[str] = []

    def run(argv: Any) -> Any:
        if argv[1] == "send":
            seen.append(Path(argv[argv.index("--prompt-file") + 1]).read_text())  # the file exists while `send` runs
        return fake(argv)

    fake.replies["send"] = '{"agentId":"a1b2c3d4-0001","status":"sent"}'
    assert orch.PaseoHost(run).wake("ABC-7", WAKE_TEXT)
    [call] = paseo_send_calls(fake)
    assert call[:3] == ["paseo", "send", "a1b2c3d4-0001"] and "--no-wait" in call and "--behavior" not in call
    assert seen == [WAKE_TEXT] and WAKE_TEXT not in call  # the text goes through a file, never the command line
    assert not Path(call[call.index("--prompt-file") + 1]).exists()  # and the file is gone after


@pytest.mark.parametrize("status", ["running", "initializing", "something-new"])
def test_the_paseo_host_never_sends_to_an_agent_that_works_or_whose_state_is_unknown(status: str) -> None:
    fake = FakePaseo()
    fake.replies["ls"] = paseo_agents(("a1b2c3d4-0001", "ABC-7 ·", status, "2026-11-09T22:00:00Z"))
    fake.replies["send"] = ""
    assert not orch.PaseoHost(fake).wake("ABC-7", WAKE_TEXT)
    assert paseo_send_calls(fake) == []  # a send would steer the turn and stop its running tool call


def test_the_paseo_host_wakes_the_newest_session_of_the_key_and_no_other_key() -> None:
    fake = FakePaseo()
    fake.replies["ls"] = paseo_agents(("a1b2c3d4-0001", "ABC-7 ·", "idle", "2026-11-09T22:00:00Z"),
                                      ("a1b2c3d4-0002", "ABC-7 · #2", "idle", "2026-11-09T23:00:00Z"),
                                      ("a1b2c3d4-0003", "ABC-8 ·", "idle", "2026-11-09T23:30:00Z"),
                                      ("a1b2c3d4-0004", "my own agent", "idle", "2026-11-09T23:40:00Z"))
    fake.replies["send"] = ""
    assert orch.PaseoHost(fake).wake("ABC-7", WAKE_TEXT)
    assert [c[2] for c in paseo_send_calls(fake)] == ["a1b2c3d4-0002"]
    fake.replies["ls"] = paseo_agents(("a1b2c3d4-0001", "ABC-7 ·", "running", "2026-11-09T22:00:00Z"),
                                      ("a1b2c3d4-0002", "ABC-7 · #2", "idle", "2026-11-09T22:00:00Z"))
    assert orch.PaseoHost(fake).wake("ABC-7", WAKE_TEXT)  # the newest by the label's number when the times tie
    assert [c[2] for c in paseo_send_calls(fake)][-1] == "a1b2c3d4-0002"


def test_the_paseo_host_finds_no_agent_for_a_key_with_none_and_never_lists_the_archived() -> None:
    fake = FakePaseo()
    fake.replies["ls"] = paseo_agents(("a1b2c3d4-0001", "ABC-8 ·", "idle", "2026-11-09T22:00:00Z"))
    assert not orch.PaseoHost(fake).wake("ABC-7", WAKE_TEXT)
    assert paseo_send_calls(fake) == []
    # `paseo ls` without `-a` lists no archived agent, so an id from it never un-archives one (a send would)
    assert all(c[:2] == ["paseo", "ls"] and "-a" not in c and "--all" not in c for c in fake.calls)


def test_the_paseo_host_answers_false_for_agent_not_found_and_raises_for_any_other_failure() -> None:
    fake = FakePaseo()
    fake.replies["ls"] = paseo_agents(("a1b2c3d4-0001", "ABC-7 ·", "idle", "2026-11-09T22:00:00Z"))

    def gone(argv: Any) -> Any:
        if argv[1] == "send":
            return orch.subprocess.CompletedProcess(argv, 1, "", '{"error":{"code":"SEND_FAILED","message":"Failed to send message: Agent not found: a1b2c3d4-0001"}}')
        return fake(argv)

    assert not orch.PaseoHost(gone).wake("ABC-7", WAKE_TEXT)

    def down(argv: Any) -> Any:
        if argv[1] == "send":
            return orch.subprocess.CompletedProcess(argv, 1, "", "daemon down")
        return fake(argv)

    with pytest.raises(RuntimeError, match="paseo send failed: daemon down"):
        orch.PaseoHost(down).wake("ABC-7", WAKE_TEXT)


def test_the_paseo_host_answers_false_for_a_pi_agent_whose_process_ended() -> None:
    fake = FakePaseo()
    fake.replies["ls"] = paseo_agents(("a1b2c3d4-0001", "ABC-7 ·", "idle", "2026-11-09T22:00:00Z"))
    sends: list[Any] = []

    def ended(argv: Any) -> Any:
        if argv[1] == "send":
            sends.append(argv)
            return orch.subprocess.CompletedProcess(argv, 1, "", "Error: Failed to send message: Pi process exited. Reload agent before sending another message.")
        return fake(argv)

    assert not orch.PaseoHost(ended).wake("ABC-7", WAKE_TEXT)
    assert len(sends) == 1


def test_the_paseo_host_refuses_an_agent_id_that_could_be_an_option() -> None:
    fake = FakePaseo()
    fake.replies["ls"] = paseo_agents(("--help", "ABC-7 ·", "idle", "2026-11-09T22:00:00Z"))
    assert not orch.PaseoHost(fake).wake("ABC-7", WAKE_TEXT) and paseo_send_calls(fake) == []


class FakeHerdr:
    """`run` for the herdr host: a workspace list, a pane list and `pane run`, each with its own answer."""

    def __init__(self, status: str = "idle", labels: tuple[tuple[str, str], ...] = (("w2", "ABC-7 ·"),)) -> None:
        self.calls: list[list[str]] = []
        self.status = status
        self.agent = True  # False: the root pane's agent ended, and the pane is back at its shell
        self.labels = labels
        self.fail: set[str] = set()  # the commands ("pane-list", "pane-run") that exit 1

    def __call__(self, argv: Any) -> Any:
        self.calls.append(list(argv))
        verb = "-".join(argv[1:3])
        if verb in self.fail:
            return orch.subprocess.CompletedProcess(argv, 1, "", "pane is gone")
        if verb == "workspace-list":
            spaces = [{"workspace_id": w, "label": label, "agent_status": self.status} for w, label in self.labels]
            return orch.subprocess.CompletedProcess(argv, 0, json.dumps({"result": {"workspaces": spaces}}), "")
        if verb == "pane-list":
            agent = {"agent": "claude"} if self.agent else {"agent": None}
            panes = [{"pane_id": f"{argv[argv.index('--workspace') + 1]}:p1", "agent_status": self.status, **agent},
                     {"pane_id": "x:p2", "agent": "claude", "agent_status": "idle"}]
            return orch.subprocess.CompletedProcess(argv, 0, json.dumps({"result": {"panes": panes}}), "")
        return orch.subprocess.CompletedProcess(argv, 0, "{}", "")


def test_the_herdr_host_types_the_message_into_the_root_pane_of_an_idle_agent() -> None:
    fake = FakeHerdr()
    assert orch.HerdrHost(fake).wake("ABC-7", WAKE_TEXT)
    assert fake.calls[-1] == ["herdr", "pane", "run", "w2:p1", WAKE_TEXT]


def test_the_herdr_host_types_the_message_into_an_agent_that_shows_done() -> None:
    """A pi pane shows `done` when its turn ends, and `idle` only once a person has looked at it (measured 2026-11-23)."""
    fake = FakeHerdr("done")
    assert orch.HerdrHost(fake).wake("ABC-7", WAKE_TEXT)
    assert fake.calls[-1] == ["herdr", "pane", "run", "w2:p1", WAKE_TEXT]


def test_the_herdr_host_types_nothing_into_a_busy_agent() -> None:
    for status in ("working", "blocked", "unknown"):
        fake = FakeHerdr(status)
        assert not orch.HerdrHost(fake).wake("ABC-7", WAKE_TEXT), status
        assert not [c for c in fake.calls if c[1:3] == ["pane", "run"]], status


def test_the_herdr_host_types_nothing_into_a_root_pane_whose_agent_ended() -> None:
    """The pane is back at its shell: herdr lists no agent there. Another pane of the workspace with an idle agent is not the root pane."""
    fake = FakeHerdr("unknown")
    fake.agent = False
    assert not orch.HerdrHost(fake).wake("ABC-7", WAKE_TEXT)
    fake = FakeHerdr("idle")  # a status of idle with no agent cannot happen in herdr, and still gets no line
    fake.agent = False
    assert not orch.HerdrHost(fake).wake("ABC-7", WAKE_TEXT)
    assert not [c for c in fake.calls if c[1:3] == ["pane", "run"]]


def test_the_herdr_host_answers_false_for_a_gone_workspace_or_pane_and_for_a_key_with_none() -> None:
    assert not orch.HerdrHost(FakeHerdr(labels=(("w3", "ABC-8 ·"),))).wake("ABC-7", WAKE_TEXT)
    gone = FakeHerdr()
    gone.fail = {"pane-list"}
    assert not orch.HerdrHost(gone).wake("ABC-7", WAKE_TEXT)
    closed = FakeHerdr()
    closed.fail = {"pane-run"}  # the pane closed between the list and the run
    assert not orch.HerdrHost(closed).wake("ABC-7", WAKE_TEXT)


def test_the_herdr_host_wakes_the_newest_workspace_of_the_key_and_sends_one_line_only() -> None:
    fake = FakeHerdr(labels=(("w2", "ABC-7 ·"), ("w5", "ABC-7 · #2"), ("w3", "ABC-8 ·")))
    assert orch.HerdrHost(fake).wake("ABC-7", WAKE_TEXT)
    assert fake.calls[-1][3] == "w5:p1"
    with pytest.raises(ValueError, match="one line"):
        orch.HerdrHost(FakeHerdr()).wake("ABC-7", "two\nlines")


def test_the_process_host_has_no_session_to_wake(tmp_path: Path) -> None:
    assert not orch.ProcessHost(tmp_path).wake("ABC-7", WAKE_TEXT)


def idle_host(host: FakeHost, label: str = "ABC-7 ·", status: str = "idle") -> None:
    host.spaces = [{"workspace_id": "w1", "label": label, "agent_status": status}]


def test_the_session_is_woken_when_the_event_is_seen_and_the_new_session_starts_only_after_the_wait(tmp_path: Path) -> None:
    runner, host, writes, clock = runner_of(tmp_path)
    host.wakeable = True
    idle_host(host)
    issues, prs = [waiting_issue()], [pr(7, "abc-7-x", reviews=ASKED)]
    clock.now = T0 + 60
    assert runner.fix_round(issues, prs, dry=False) == ["woke the session of ABC-7 (changes requested)"]
    assert host.wakes == [("ABC-7", WAKE_TEXT)] and host.starts == [] and writes.done == []  # no Linear comment for a wake
    clock.now = T0 + 299
    assert runner.fix_round(issues, prs, dry=False) == [] and host.starts == []
    clock.now = T0 + 300  # no claim in 5 minutes: the new session, as before
    assert runner.fix_round(issues, prs, dry=False) == ["started a fix session for ABC-7 (claude-sonnet-5-5, medium)"]
    assert len(host.wakes) == 1 and len(host.starts) == 1  # still one wake
    clock.now = T0 + 600  # and a second wait without a claim escalates, as before
    assert runner.fix_round([waiting_issue(fix_comment(T0 + 300))], prs, dry=False)[0].startswith("escalated ABC-7")


def test_a_wake_goes_out_for_each_cause(tmp_path: Path) -> None:
    for why, make in CAUSES.items():
        runner, host, _, clock = runner_of(tmp_path)
        host.wakeable = True
        idle_host(host)
        clock.now = T0 + 60
        assert runner.fix_round([waiting_issue()], [make()], dry=False) == [f"woke the session of ABC-7 ({why})"], why
        assert host.wakes == [("ABC-7", orch.wake_text(7, why))], why


def test_a_busy_session_gets_no_message_and_the_next_round_tries_again(tmp_path: Path) -> None:
    runner, host, _, clock = runner_of(tmp_path)
    host.wakeable = True
    idle_host(host, status="working")
    issues, prs = [waiting_issue()], [pr(7, "abc-7-x", reviews=ASKED)]
    clock.now = T0 + 60
    assert runner.fix_round(issues, prs, dry=False) == [] and host.wakes == []
    idle_host(host)  # the turn ended
    clock.now = T0 + 120
    assert runner.fix_round(issues, prs, dry=False) == ["woke the session of ABC-7 (changes requested)"]
    assert len(host.wakes) == 1


def test_a_session_that_was_busy_until_the_wait_ended_gets_a_new_session_and_no_wake(tmp_path: Path) -> None:
    """A round that wakes the old session never also starts a new one for the same event."""
    runner, host, _, clock = runner_of(tmp_path)
    host.wakeable = True
    issues, prs = [waiting_issue()], [pr(7, "abc-7-x", reviews=ASKED)]
    for later in (60, 120, 240):
        clock.now = T0 + later
        idle_host(host, status="working")
        assert runner.fix_round(issues, prs, dry=False) == []
    idle_host(host)  # the turn ended just as the wait did
    clock.now = T0 + 300
    assert runner.fix_round(issues, prs, dry=False) == ["started a fix session for ABC-7 (claude-sonnet-5-5, medium)"]
    assert host.wakes == [] and len(host.starts) == 1
    # a restart that lost the memory of the wake does not wake after the wait either
    runner, host, _, clock = runner_of(tmp_path)
    host.wakeable = True
    idle_host(host)
    clock.now = T0 + 400
    assert runner.fix_round(issues, prs, dry=False)[0].startswith("started a fix session") and host.wakes == []
    # a dry run neither: it says `would start a fix session` only
    runner, host, _, clock = runner_of(tmp_path)
    idle_host(host)
    clock.now = T0 + 300
    assert [e.kind for e in runner.plan(issues, prs, dry=True)[1]] == ["would_start_fix"]


def test_no_wake_when_the_host_list_cannot_be_read(tmp_path: Path) -> None:
    class NoList(FakeHost):
        def workspaces(self) -> list[dict[str, Any]]:
            raise RuntimeError("herdr workspace list failed")

    host, clock = NoList(), Clock()
    host.wakeable = True
    runner = orch.Runner(fix_cfg(tmp_path), host, None, clock)
    clock.now = T0 + 60
    assert runner.fix_round([waiting_issue()], [pr(7, "abc-7-x", reviews=ASKED)], dry=False) == [] and host.wakes == []


def test_one_wake_for_each_event_and_a_new_event_gets_a_new_wake(tmp_path: Path) -> None:
    runner, host, _, clock = runner_of(tmp_path)
    host.wakeable = True
    idle_host(host)
    issues, first = [waiting_issue()], [pr(7, "abc-7-x", reviews=ASKED)]
    for later in (60, 120, 180, 240):
        clock.now = T0 + later
        runner.fix_round(issues, first, dry=False)
    assert len(host.wakes) == 1
    # the session fixed and pushed, and the next round asked for changes again: a new event, a new wake
    second_at = "2026-11-02T11:00:00Z"
    second = [pr(7, "abc-7-x", reviews=(("CHANGES_REQUESTED", second_at),), commit="2026-11-02T10:30:00Z")]
    clock.now = T0 + 3600 + 60
    assert runner.fix_round(issues, second, dry=False) == ["woke the session of ABC-7 (changes requested)"]
    assert len(host.wakes) == 2


def test_a_fix_that_was_pushed_after_the_review_wakes_nobody(tmp_path: Path) -> None:
    runner, host, _, clock = runner_of(tmp_path)
    host.wakeable = True
    idle_host(host)
    clock.now = T0 + 60
    pushed = [pr(7, "abc-7-x", reviews=ASKED, commit="2026-11-02T10:01:00Z")]
    assert runner.fix_round([waiting_issue()], pushed, dry=False) == [] and host.wakes == []


def test_a_draft_pull_request_and_the_restart_label_wake_nobody(tmp_path: Path) -> None:
    runner, host, _, clock = runner_of(tmp_path)
    host.wakeable = True
    idle_host(host)
    clock.now = T0 + 60
    assert runner.fix_round([waiting_issue()], [pr(7, "abc-7-x", draft=True, reviews=ASKED)], dry=False) == []
    assert runner.fix_round([waiting_issue(labels=("restart",))], [pr(7, "abc-7-x")], dry=False) == []
    assert host.wakes == []


def test_a_session_that_the_host_cannot_reach_leaves_no_event_and_the_ladder_goes_on(tmp_path: Path) -> None:
    runner, host, _, clock = runner_of(tmp_path)  # `wake` answers False
    idle_host(host)
    issues, prs = [waiting_issue()], [pr(7, "abc-7-x", reviews=ASKED)]
    clock.now = T0 + 60
    assert runner.fix_round(issues, prs, dry=False) == []
    clock.now = T0 + 120
    runner.fix_round(issues, prs, dry=False)
    assert len(host.wakes) == 2  # tried again each round
    clock.now = T0 + 300
    assert runner.fix_round(issues, prs, dry=False) == ["started a fix session for ABC-7 (claude-sonnet-5-5, medium)"]
    assert runner.woke == {}


def test_a_failed_wake_prints_once_for_the_wait_and_the_ladder_goes_on(tmp_path: Path) -> None:
    runner, host, _, clock = runner_of(tmp_path)
    host.wake_error = RuntimeError("paseo send failed: daemon down")
    idle_host(host)
    issues, prs = [waiting_issue()], [pr(7, "abc-7-x", reviews=ASKED)]
    clock.now = T0 + 60
    assert runner.fix_round(issues, prs, dry=False) == ["wake of the session of ABC-7 failed: RuntimeError: paseo send failed: daemon down"]
    clock.now = T0 + 120
    assert runner.fix_round(issues, prs, dry=False) == [] and len(host.wakes) == 2  # tried again, not printed again
    clock.now = T0 + 300
    assert runner.fix_round(issues, prs, dry=False) == ["started a fix session for ABC-7 (claude-sonnet-5-5, medium)"]


def test_no_wake_for_an_event_whose_fix_session_was_started(tmp_path: Path) -> None:
    runner, host, _, clock = runner_of(tmp_path)
    host.wakeable = True
    idle_host(host)
    clock.now = T0 + 330
    started = waiting_issue(fix_comment(T0 + 300))  # the orchestrator started a session for this event
    assert runner.fix_round([started], [pr(7, "abc-7-x", reviews=ASKED)], dry=False) == [] and host.wakes == []


def test_a_dry_run_says_would_wake_once_and_sends_nothing(tmp_path: Path) -> None:
    runner, host, writes, clock = runner_of(tmp_path)
    host.wakeable = True
    idle_host(host)
    prs = [pr(7, "abc-7-x", reviews=ASKED)]
    clock.now = T0 + 60
    plan, events = runner.plan([waiting_issue()], prs, dry=True)
    assert [e.kind for e in events] == ["would_wake"] and plan["events"][0]["event"] == "would_wake"
    assert "would wake the session of ABC-7 (changes requested)" in orch.plan_text(plan, events)
    assert [e["event"] for e in orch.shown_state(plan)["quiet"]] == ["would_wake"]  # a quiet event: the loop prints it when it changes
    clock.now = T0 + 120
    assert runner.plan([waiting_issue()], prs, dry=True)[1] == []  # once for the event
    assert host.wakes == [] and host.starts == [] and writes.done == []


def test_a_dry_run_says_nothing_for_a_busy_session_or_none_in_the_list(tmp_path: Path) -> None:
    for spaces in ([], [{"workspace_id": "w1", "label": "ABC-7 ·", "agent_status": "working"}]):
        runner, host, _, clock = runner_of(tmp_path)
        host.spaces = spaces
        clock.now = T0 + 60
        assert runner.plan([waiting_issue()], [pr(7, "abc-7-x", reviews=ASKED)], dry=True)[1] == []


def test_a_wake_event_reaches_the_plan_and_its_json_line(tmp_path: Path) -> None:
    runner, host, _, clock = runner_of(tmp_path)
    host.wakeable = True
    idle_host(host)
    clock.now = T0 + 60
    plan, _ = runner.plan([waiting_issue()], [pr(7, "abc-7-x", reviews=ASKED)], dry=False)
    assert [e["event"] for e in plan["events"]] == ["woke"]
    assert any('"woke"' in line for line in orch.event_json(plan))


# -- Each open pull request's state, and what waits on the owner ------------------------------------------------

def ago(minutes: float) -> str:
    return datetime.fromtimestamp(T0 - minutes * 60, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def done(name: str, minutes: float, conclusion: str = "SUCCESS") -> dict[str, Any]:
    return {"name": name, "status": "COMPLETED", "conclusion": conclusion, "startedAt": ago(minutes + 5), "completedAt": ago(minutes)}


def open_pr(number: int = 7, draft: bool = False, commit: float = 5, checks: tuple[dict[str, Any], ...] = (),
            reviews: tuple[tuple[str, str], ...] = (), labels: tuple[str, ...] = (), comments: tuple[tuple[str, str], ...] = ()) -> dict[str, Any]:
    found = pr(number, f"abc-{number}-x", draft=draft, reviews=reviews, commit=ago(commit))
    return dict(found, statusCheckRollup=list(checks), labels=[{"name": x} for x in labels],
                comments=[{"body": b, "createdAt": t, "author": {"login": UPDATER}} for b, t in comments])


def status(pr_: dict[str, Any], state: str = "In Progress", busy: Any = frozenset(), comments: tuple[tuple[str, str], ...] = ()) -> tuple[str, str]:
    found = dict(issue("ABC-7", state=state), comments=[{"body": b, "created": t} for b, t in comments])
    got = orch.pr_status(found, pr_, CFG, T0, busy)
    return got[0], got[1]


APPROVE = (("APPROVED", ago(10)),)


def test_each_normal_state_of_a_pull_request() -> None:
    assert status(open_pr(draft=True), busy={"ABC-7"}) == ("building", "")
    assert status(open_pr()) == ("in review", "")  # ready 5 min after its last commit, no review run yet
    assert status(open_pr(checks=(run("verdict", "", ago(4), "IN_PROGRESS"),)), "In Review") == ("in review", "")
    assert status(open_pr(reviews=(("CHANGES_REQUESTED", ago(1)),))) == ("needs a fix", "changes requested")
    assert status(open_pr(checks=(done("python", 1, "FAILURE"),))) == ("needs a fix", "failed")
    green = (done("python", 3), done("web", 2), done("verdict", 1))
    assert status(open_pr(reviews=APPROVE, checks=green), "Approved") == ("approved, waiting for the merge", "")
    running = (done("python", 3), run("web", "", ago(4), "IN_PROGRESS"), done("verdict", 1))
    assert status(open_pr(reviews=APPROVE, checks=running, commit=90), "Approved") == ("approved, waiting for the merge", "")


def test_each_stale_reason_needs_its_time_limit() -> None:
    assert status(open_pr(draft=True, commit=29)) == ("building", "")
    assert status(open_pr(draft=True, commit=31)) == ("stale", "draft, no session busy, no commit in 30 min")
    assert status(open_pr(draft=True, commit=31), busy=None) == ("building", "")  # no list read: nothing is stale for want of a session
    assert status(open_pr(commit=21), "In Review") == ("stale", "no review run 20 min after the last commit")
    old_run = (run("verdict", "", ago(61), "IN_PROGRESS"),)
    assert status(open_pr(checks=old_run, commit=70), "In Review") == ("stale", "review run not ended in 60 min")
    green = (done("python", 40), done("web", 35), done("verdict", 31))
    assert status(open_pr(reviews=APPROVE, checks=green, commit=50), "Approved") == ("stale", "approved and green, not merged in 30 min")
    asked = open_pr(reviews=(("CHANGES_REQUESTED", ago(24 * 60 + 1)),), commit=24 * 60 + 5)
    assert status(asked) == ("stale", "no commit in 24 h, no session busy")
    assert status(asked, busy={"ABC-7"}) == ("needs a fix", "changes requested")
    fixed = (("CHANGES_REQUESTED", ago(30)),)  # a fix pushed after the request: it waits for its next review, not for a fix
    assert status(open_pr(reviews=fixed, commit=21), "In Review") == ("stale", "no review run 20 min after the last commit")
    assert status(open_pr(reviews=fixed, commit=21, checks=(run("verdict", "", ago(1), "IN_PROGRESS"),)), "In Review") == ("in review", "")


def test_a_stale_pull_request_goes_back_to_a_normal_state() -> None:
    assert status(open_pr(commit=25), "In Review")[0] == "stale"
    assert status(open_pr(commit=25, checks=(run("verdict", "", ago(1), "IN_PROGRESS"),)), "In Review") == ("in review", "")  # a review started
    assert status(open_pr(draft=True, commit=40))[0] == "stale"
    assert status(open_pr(draft=True, commit=40), busy={"ABC-7"}) == ("building", "")  # a session took it again


def test_what_waits_on_the_owner_and_why() -> None:
    assert status(open_pr(), "Escalated") == ("waits on the owner", "not clear: read the issue")
    assert status(open_pr(labels=("escalated",)), "Approved") == ("waits on the owner", "not clear: read the issue")
    owned = "The reviewer approved PR #7. It changes files that only the owner merges (.github/CODEOWNERS, ...):"
    assert status(open_pr(), "Escalated", comments=((owned, ago(2)),)) == ("waits on the owner", "code owner's file: owner-approved or merge")
    refused = "The reviewer approved PR #7, but GitHub refused to turn on auto-merge 5 times."
    assert status(open_pr(), "Escalated", comments=((owned, ago(9)), (refused, ago(2)))) == ("waits on the owner", "auto-merge refused: rerun the job or merge")
    merge = "Ready to merge. It changes a workflow file, which the app cannot merge: the owner merges it on GitHub."
    assert status(open_pr(comments=((merge, ago(2)),)), "Approved") == ("waits on the owner", "workflow change: merge it")
    assert status(open_pr(commit=1, comments=((merge, ago(2)),)), "Approved")[0] == "in review"  # a commit came after it
    for said, reason in (("The gates passed on a rerun at abc1234, so the check verdict passes again with the same approve. PR #7 changes a "
                          "file of CODEOWNERS, so it gets no auto-merge", "code owner's file, green on a rerun: merge"),
                         ("The gates failed on this approved head abc1234.", "failed gates on an approved head: rerun"),
                         (orch.ESCALATION + " (changes requested), and no session took it", "no session took the fix")):
        assert status(open_pr(), "Escalated", comments=((said, ago(2)),)) == ("waits on the owner", reason)
    blocked = "**Round 4 is not reviewed: three rounds is the maximum.** The issue waits for you in Escalated."
    assert status(open_pr(comments=((blocked, ago(2)),)), "Escalated") == ("waits on the owner", "blocked after the last round")
    # R1-1: an old reason from a wait that ended (before the fix's commit) gives way to the reviewer's needs-decision review
    asked = dict(open_pr(commit=10, comments=(("The gates failed on this approved head abc1234.", ago(30)),)),
                 reviews=[{"state": "CHANGES_REQUESTED", "submittedAt": ago(3), "body": "Needs decision: which limit holds?"}])
    old = ((orch.ESCALATION + " (changes requested), and no session took it", ago(20)), (blocked, ago(15)))
    assert status(asked, "Escalated", comments=old) == ("waits on the owner", "the reviewer's question: answer on the issue")
    asked["reviews"] = []  # no artifact newer than the last commit: no reason from the ended waits
    assert status(asked, "Escalated", comments=old) == ("waits on the owner", "not clear: read the issue")


def test_a_conflict_sends_a_pull_request_that_waited_for_the_owners_merge_back_to_its_session() -> None:
    """The branch update's conflict sends such a pull request back to its session as
    any other. The issue in In Progress resumes, and the pull request stops waiting on the owner: its workflow change's
    `WORKFLOW_MERGE` comment would keep it there, and the conflict is newer."""
    merge = "Ready to merge. It changes a workflow file, which the app cannot merge: the owner merges it on GitHub."
    found = open_pr(commit=5, comments=((merge, ago(4)), (CONFLICT, ago(2))))
    assert status(found) == ("needs a fix", "conflict with main")
    assert orch.can_resume(issue("ABC-7", state="In Progress", labels=("escalated",)), found, CFG) == "conflict with main", \
        "a label that a failed write left behind holds nothing back"
    # after the fix's push the conflict is older than the last commit: the pull request waits for the owner's merge again, as
    # the review's approve goes back to Escalated
    fixed = open_pr(commit=5, comments=((merge, ago(4)), (CONFLICT, ago(6))))
    assert status(fixed, "Escalated") == ("waits on the owner", "workflow change: merge it")
    # a conflict on a pull request that waited for the owner's merge because of a code owner's file other than a workflow
    assert status(open_pr(commit=5, comments=((CONFLICT, ago(2)),))) == ("needs a fix", "conflict with main")


def test_the_plan_lists_the_pull_requests_and_what_waits_on_the_owner(tmp_path: Path) -> None:
    issues = [issue("ABC-7", state="In Review", area="infra"), dict(issue("ABC-8", state="Escalated"), comments=[]),
              issue("ABC-9", state="Escalated", area="infra"), issue("ABC-10", state="In Progress", area="docs")]
    runner, host, _, _ = runner_of(tmp_path)
    host.spaces = [{"workspace_id": "w1", "label": "ABC-10 ·", "agent_status": "working"}]
    prs = [open_pr(7, commit=25), open_pr(9), open_pr(10, draft=True, commit=40)]
    plan, events = runner.plan(issues, prs, dry=True)
    assert plan["version"] == orch.PLAN_VERSION
    assert [(p["key"], p["number"], p["state"], p["since"]) for p in plan["prs"]] == [
        ("ABC-7", 7, "stale", ago(25)), ("ABC-9", 9, "waits on the owner", None), ("ABC-10", 10, "building", ago(40))]
    assert plan["yours"] == [{"key": "ABC-8", "pr": None, "reason": "a question on the issue"},
                             {"key": "ABC-9", "pr": 9, "reason": "not clear: read the issue"}]
    text = orch.plan_text(plan, events)
    assert "you 2" in text[0] and any(line.startswith("#7 ") and line.endswith("ABC-7 stale: no review run 20 min after the last commit") for line in text)
    assert not any(line.startswith("#9 ") for line in text)  # it waits on the owner: the row `yours` has it
    assert text[-1].startswith("yours") and text[-1].endswith("ABC-8 a question on the issue · ABC-9 #9 not clear: read the issue")
    assert orch.queue_lines(plan)[-1] == text[-1] and orch.queue_lines(plan)[0] == "build 1/2 · review 1 · you 2"
    assert orch.shown_state(plan)["prs"][0] == {"key": "ABC-7", "number": 7, "state": "stale", "reason": "no review run 20 min after the last commit"}
    host.spaces = []  # the session ended: its draft is stale now
    assert [p["state"] for p in runner.plan(issues, prs, dry=True)[0]["prs"]] == ["stale", "waits on the owner", "stale"]


def test_the_view_shows_the_pull_requests_and_what_waits_on_the_owner_at_40_columns() -> None:
    plan = dict(VIEW_PLAN, version=3, yours=[{"key": "ABC-9", "pr": 9, "reason": "code owner's file: owner-approved or merge"}],
                prs=[{"key": "ABC-677", "number": 897, "state": "in review", "reason": "", "since": "2026-11-10T18:18:12Z"},
                     {"key": "ABC-647", "number": 898, "state": "stale", "reason": "draft, no session busy, no commit in 30 min",
                      "since": "2026-11-10T17:00:00Z"},
                     {"key": "ABC-9", "number": 9, "state": "waits on the owner", "reason": "x", "since": None}])
    lines = view(40, plan=plan)
    assert lines[0] == "18:30 · b 1/3 · r 1 · you 1 · 12 s ago" and all(len(line) <= 40 for line in lines)
    assert view(40, color=True, plan=plan)[0] == "18:30 · b 1/3 · r 1 · \x1b[1;33myou 1\x1b[0m · \x1b[90m12 s ago\x1b[0m"
    assert view(30, plan=plan)[0] == "18:30 · b 1/3 · r 1 · you 1"  # the age goes last
    assert view(40, color=True, now=VIEW_NOW + 169, plan=plan)[0].endswith("\x1b[31m3 min ago\x1b[0m")  # a stale age stays red
    assert lines[-7:] == ["yours", "  ABC-9 #9 code owner's file: owner-app…", "pull requests", "  #897 ABC-677 (12 min) in review",
                          "  #898 ABC-647 (1 h) stale: draft, no s…", "other", "  ABC-689 no Area label: add one"]
    painted = view(40, color=True, plan=plan)
    assert "\x1b[31m  #898 ABC-647 (1 h) stale: draft, no s…\x1b[0m" in painted and "\x1b[1;33m  ABC-9 #9 code owner's file: owner-app…\x1b[0m" in painted
    assert view(40, height=9, plan=plan)[-3:] == ["yours", "  ABC-9 #9 code owner's file: owner-app…", "  +3 more"]  # names are no lines
    assert view(40, height=8, plan=plan)[-1] == "● ABC-647"  # no room for a name and a line: no section


def test_the_view_reads_a_plan_from_before_the_list_of_yours() -> None:
    assert "you 3" in view(54, plan=dict(VIEW_PLAN, yours=3))[0]
    assert orch.your_count({"yours": 2}) == 2 and "yours" not in "\n".join(orch.plan_text(dict(VIEW_PLAN, yours=2, areas=[]), []))


# -- The pause while the newest Paseo session stopped on an API error --------------------------------------------

LIMIT_LOG = "[User] /implement ABC-9\n[Assistant] Reading the issue.\n[System Error] You've hit your limit · resets 3pm (Europe/Lisbon)\n"
KEY_LOG = "[User] /implement ABC-9\n[System Error] Invalid API key · Fix external API key\n"  # row 6 of the table of reasons, word for word
CRASH_LOG = "[User] /implement ABC-9\n[System Error] Claude stopped unexpectedly (signal SIGKILL)\n"  # row 4 of the table of reasons


def paseo_with(log: str, *agents: tuple[str, str, str, str]) -> FakePaseo:
    fake = FakePaseo()
    fake.replies["ls"] = paseo_agents(*agents)
    fake.replies["logs"] = log
    return fake


def claude_paseo(fake: Any) -> Any:
    """A Paseo host with the stop patterns of the example's `claude` harness."""
    return orch.PaseoHost(fake, EXAMPLE.stop_patterns)


@pytest.mark.parametrize(("log", "cause"), [(LIMIT_LOG, "the usage limit"), (KEY_LOG, "an invalid API key"),
                                            ("[System Error] Claude AI usage limit reached|1763380800\n", "the usage limit")])
def test_the_paseo_host_pauses_when_the_newest_session_stopped_on_the_limit_or_a_bad_key(log: str, cause: str) -> None:
    fake = paseo_with(log, ("a1b2c3d4-0001", "ABC-7 ·", "idle", "2 hours ago"),
                      ("a1b2c3d4-0002", "ABC-9 ·", "error", "3 minutes ago"),
                      ("a1b2c3d4-0003", "my own agent", "running", "1 minutes ago"))  # the owner's agent is not the orchestrator's
    assert claude_paseo(fake).paused() == {None: ("ABC-9", cause)}
    assert [c for c in fake.calls if c[1] == "logs"] == [["paseo", "logs", "a1b2c3d4-0002", "--tail", "20"]]


@pytest.mark.parametrize("log", [CRASH_LOG, "[System Error] API Error: Your computer went to sleep mid-response.\n",
                                 "[Assistant] The issue says the usage limit resets each day.\n", ""])
def test_the_paseo_host_does_not_pause_on_another_error_or_on_words_outside_a_system_error(log: str) -> None:
    fake = paseo_with(log, ("a1b2c3d4-0002", "ABC-9 ·", "error", "3 minutes ago"))
    assert claude_paseo(fake).paused() == {}


def test_the_paseo_host_reads_only_the_newest_session_of_its_label() -> None:
    # an older session stopped on the limit, and a later one works: no pause, and no log read
    fake = paseo_with(LIMIT_LOG, ("a1b2c3d4-0001", "ABC-7 ·", "error", "9 hours ago"),
                      ("a1b2c3d4-0002", "ABC-9 ·", "running", "16 minutes ago"))
    assert claude_paseo(fake).paused() == {}
    assert [c for c in fake.calls if c[1] == "logs"] == []
    # the owner sent to the stopped agent: it is running or idle, and the pause ends
    for status in ("running", "idle"):
        assert claude_paseo(paseo_with(LIMIT_LOG, ("a1b2c3d4-0002", "ABC-9 ·", status, "3 minutes ago"))).paused() == {}
    # of two of the same age, one that is not in error is the newest
    assert claude_paseo(paseo_with(LIMIT_LOG, ("a1b2c3d4-0001", "ABC-7 ·", "error", "3 minutes ago"),
                                     ("a1b2c3d4-0002", "ABC-9 ·", "idle", "3 minutes ago"))).paused() == {}
    assert claude_paseo(paseo_with(LIMIT_LOG)).paused() == {}  # no agent of its label
    assert claude_paseo(paseo_with(LIMIT_LOG, ("a1b2c3d4-0002", "ABC-9 ·", "error", "3 minutes ago"))).paused() == {None: ("ABC-9", "the usage limit")}


def test_the_paseo_host_raises_on_a_failed_list_or_log_and_the_round_keeps_the_pause(tmp_path: Path) -> None:
    fake = paseo_with(LIMIT_LOG, ("a1b2c3d4-0002", "ABC-9 ·", "error", "3 minutes ago"))
    runner = orch.Runner(fix_cfg(tmp_path), claude_paseo(fake), None, Clock())
    assert [e.kind for e in runner.plan([], [], dry=True)[1]] == ["paused"]
    fake.codes["logs"] = 1
    with pytest.raises(RuntimeError):
        claude_paseo(fake).paused()
    assert runner.plan([issue("ABC-3", area="docs")], [], dry=True)[1] == []  # no `resumed` and no start
    assert runner.stops.pause == {None: ("ABC-9", "the usage limit")}
    with pytest.raises(RuntimeError):
        claude_paseo(FakePaseo(ls=1)).paused()


def test_the_herdr_and_process_hosts_never_pause(tmp_path: Path) -> None:
    def run(argv: Any) -> Any:
        raise AssertionError(f"no command: {argv}")
    assert orch.HerdrHost(run).paused() == {}
    assert orch.ProcessHost(tmp_path).paused() == {}


@pytest.mark.parametrize(("text", "seconds"), [("1 minutes ago", 60), ("16 minutes ago", 960), ("9 hours ago", 9 * 3600),
                                               ("3 days ago", 3 * 86400), ("an hour ago", 3600), ("just now", 0),
                                               ("2026-11-02T09:00:00Z", 3600), ("soon", orch.math.inf), (None, orch.math.inf)])
def test_created_age_reads_paseos_relative_text_and_an_iso_time(text: object, seconds: float) -> None:
    assert orch.created_age(text, now=T0) == seconds


def test_the_paseo_host_wakes_the_youngest_agent_by_its_relative_created_text() -> None:
    """A comment at 09:49: as text, "9 hours ago" > "2 minutes ago", so `max` over the text took the older agent."""
    fake = FakePaseo()
    fake.replies["ls"] = paseo_agents(("a1b2c3d4-0001", "ABC-7 ·", "idle", "9 hours ago"),
                                      ("a1b2c3d4-0002", "ABC-7 · #2", "idle", "2 minutes ago"))
    fake.replies["send"] = ""
    assert orch.PaseoHost(fake).wake("ABC-7", WAKE_TEXT)
    # a restart of the orchestrator numbers from 1 again: the age decides before the label's number
    fake.replies["ls"] = paseo_agents(("a1b2c3d4-0003", "ABC-7 · #2", "idle", "3 days ago"),
                                      ("a1b2c3d4-0004", "ABC-7 ·", "idle", "16 minutes ago"))
    assert orch.PaseoHost(fake).wake("ABC-7", WAKE_TEXT)
    assert [c[2] for c in paseo_send_calls(fake)] == ["a1b2c3d4-0002", "a1b2c3d4-0004"]


def test_a_pause_stops_starts_wakes_and_escalations_and_says_why_once(tmp_path: Path) -> None:
    runner, host, writes, clock = runner_of(tmp_path)
    host.wakeable = True
    idle_host(host)
    host.pause = ("ABC-9", "the usage limit")
    issues = [waiting_issue(), issue("ABC-3", area="docs")]  # a fix wait, and a Todo issue that could start
    prs = [pr(7, "abc-7-x", reviews=ASKED)]
    for now in (T0 + 60, T0 + 300, T0 + 600, T0 + 900):  # the wake's time, the new session's, the escalation's, and later
        clock.now = now
        plan, events = runner.plan(issues, prs, dry=False)
        expected = ["paused"] if now == T0 + 60 else []
        assert [e.kind for e in events] == expected, now
        assert [e["event"] for e in plan["events"]] == expected
    assert host.wakes == [] and host.starts == [] and writes.done == []
    assert events == [] and runner.stops.pause == {None: ("ABC-9", "the usage limit")}
    first = runner.plan(issues, prs, dry=False)  # still paused: no second event
    assert first[1] == []


def test_a_fix_wait_counts_from_the_end_of_the_pause(tmp_path: Path) -> None:
    """GitHub's times keep running in a pause, but the session that the owner sent to has its wait from the resume."""
    runner, host, writes, clock = runner_of(tmp_path)
    idle_host(host)
    host.pause = ("ABC-9", "the usage limit")
    issues = [waiting_issue()]
    prs = [pr(7, "abc-7-x", reviews=ASKED)]
    clock.now = T0 + 60
    runner.plan(issues, prs, dry=False)
    host.pause = None
    clock.now = T0 + 3600  # the wait is an hour old, the pause ends now
    events = runner.plan(issues, prs, dry=False)[1]
    assert [e.kind for e in events] == ["resumed"]
    assert host.starts == [] and writes.done == []
    clock.now = T0 + 3600 + 300  # the wait from the resume is over: one new session
    assert [e.kind for e in runner.plan(issues, prs, dry=False)[1]] == ["started_fix"]


def test_a_fix_session_started_before_a_pause_has_its_wait_from_the_resume(tmp_path: Path) -> None:
    """The floor on `began` keeps the right reason and wakes nobody in the first round after the pause."""
    runner, host, writes, clock = runner_of(tmp_path)
    host.wakeable = True
    idle_host(host)
    issues, prs = [waiting_issue()], [pr(7, "abc-7-x", reviews=ASKED)]
    clock.now = T0 + 300
    assert [e.kind for e in runner.plan(issues, prs, dry=False)[1]] == ["started_fix"]
    host.pause = ("ABC-9", "the usage limit")
    clock.now = T0 + 400
    assert [e.kind for e in runner.plan(issues, prs, dry=False)[1]] == ["paused"]
    host.pause = None
    clock.now = T0 + 3600
    assert [e.kind for e in runner.plan(issues, prs, dry=False)[1]] == ["resumed"]
    assert host.wakes == [] and not any(c for c in writes.done if "escalat" in str(c).lower())
    clock.now = T0 + 3900
    events = runner.plan(issues, prs, dry=False)[1]
    assert [e.kind for e in events] == ["escalated"]
    assert "the session started for the fix did not take it" in events[0].text


def test_a_dry_round_says_paused_and_would_do_nothing(tmp_path: Path) -> None:
    runner, host, _, clock = runner_of(tmp_path)
    idle_host(host)
    host.pause = ("ABC-9", "the usage limit")
    clock.now = T0 + 300
    plan, events = runner.plan([waiting_issue(), issue("ABC-3", area="docs")], [pr(7, "abc-7-x", reviews=ASKED)], dry=True)
    assert [(e.kind, e.key, e.reason) for e in events] == [("paused", "ABC-9", "the usage limit")]
    assert events[0].text.startswith("paused: the usage limit") and "of ABC-9" in events[0].text
    assert "until the owner sends to that agent or archives it" in events[0].text  # R1-1: no later session starts while paused
    assert any('"paused"' in line for line in orch.event_json(plan))


def test_the_pause_ends_when_no_session_shows_the_stop_and_the_round_goes_on(tmp_path: Path) -> None:
    runner, host, _, clock = runner_of(tmp_path)
    host.pause = ("ABC-9", "the usage limit")
    clock.now = T0 + 60
    assert [e.kind for e in runner.plan([issue("ABC-3", area="docs")], [], dry=False)[1]] == ["paused"]
    assert host.starts == []
    host.pause = None  # the owner sent to the agent, or archived it
    clock.now = T0 + 120
    events = runner.plan([issue("ABC-3", area="docs")], [], dry=False)[1]
    assert [(e.kind, e.reason) for e in events] == [("resumed", "the usage limit"), ("started", "")]
    assert len(host.starts) == 1


def test_a_new_cause_says_paused_again_and_a_failed_read_keeps_the_pause(tmp_path: Path) -> None:
    runner, host, _, _ = runner_of(tmp_path)
    host.pause = ("ABC-9", "the usage limit")
    assert [e.kind for e in runner.plan([], [], dry=False)[1]] == ["paused"]
    host.pause = ("ABC-9", "an invalid API key")
    assert [(e.kind, e.reason) for e in runner.plan([], [], dry=False)[1]] == [("paused", "an invalid API key")]

    class Failing(FakeHost):
        def paused(self) -> dict[str | None, tuple[str, str]]:
            raise RuntimeError("paseo ls failed: daemon down")
    failing = Failing()
    runner.host = failing
    assert runner.plan([issue("ABC-3", area="docs")], [], dry=False)[1] == [] and failing.starts == []
    assert runner.stops.pause == {None: ("ABC-9", "an invalid API key")}


def test_an_error_that_is_not_a_stop_pauses_nothing_in_a_round(tmp_path: Path) -> None:
    fake = paseo_with(CRASH_LOG, ("a1b2c3d4-0002", "ABC-9 ·", "error", "3 minutes ago"))
    runner = orch.Runner(fix_cfg(tmp_path), orch.PaseoHost(fake), None, Clock())
    _, events = runner.plan([issue("ABC-3", area="docs")], [], dry=True)
    assert [e.kind for e in events] == ["would_start"] and runner.stops.pause == {}


RETRYABLE = ("ABC-9", "an invalid API key")  # the example's pair carries true: a retry can clear it


def test_a_stop_gets_one_retry_after_the_wait_and_a_recovery_ends_it(tmp_path: Path) -> None:
    """no wake before `retry_wait_minutes`, one `Retry` at it, and no pause when the stop then clears."""
    runner, host, _, clock = runner_of(tmp_path)
    host.wakeable = True
    host.pause = RETRYABLE
    clock.now = T0
    plan, events = runner.plan([issue("ABC-3", area="docs")], [], dry=False)
    assert [e.kind for e in events] == ["retrying"]
    assert events[0].text == "retrying ABC-9 after an API error (an invalid API key)"
    assert plan["pause"] == [{"key": "ABC-9", "cause": RETRYABLE[1], "phase": "retrying"}]
    assert "retrying ABC-9 after an API error (an invalid API key)" in orch.plan_text(plan, events)
    assert host.wakes == [] and host.starts == []
    clock.now = T0 + 179  # the wait is not over
    assert runner.plan([issue("ABC-3", area="docs")], [], dry=False)[1] == []
    assert host.wakes == [] and host.starts == []
    clock.now = T0 + 180  # the retry goes to the stopped session
    assert runner.plan([issue("ABC-3", area="docs")], [], dry=False)[1] == []
    assert host.wakes == [("ABC-9", orch.RETRY_TEXT)] and host.starts == []  # the pause still holds
    host.pause = None  # the session recovered under the retry
    clock.now = T0 + 240
    plan, events = runner.plan([issue("ABC-3", area="docs")], [], dry=False)
    assert events == [] and plan["pause"][0]["phase"] == "retrying"  # its window is not over: the loop still holds
    clock.now = T0 + 360  # the window after the wake ends
    events = runner.plan([issue("ABC-3", area="docs")], [], dry=False)[1]
    assert events[0].kind == "resumed" and runner.stops.pause == {} and runner.stops.retry == {}
    assert host.wakes == [("ABC-9", orch.RETRY_TEXT)] and len(host.starts) == 1  # the loop goes on


def test_a_second_stop_after_the_retry_pauses_and_gets_no_second_retry(tmp_path: Path) -> None:
    runner, host, _, clock = runner_of(tmp_path)
    host.wakeable = True
    host.pause = RETRYABLE
    assert [e.kind for e in runner.plan([], [], dry=False)[1]] == ["retrying"]
    clock.now = T0 + 180
    assert runner.plan([], [], dry=False)[1] == [] and host.wakes == [("ABC-9", orch.RETRY_TEXT)]
    clock.now = T0 + 359  # the retry's window is not over: the retry may still be taking effect
    assert runner.plan([], [], dry=False)[1] == []
    clock.now = T0 + 360  # the same stop is still there
    plan, events = runner.plan([], [], dry=False)
    assert [(e.kind, e.reason) for e in events] == [("paused", RETRYABLE[1])] and events[0].text.startswith("paused: an invalid API key")
    assert plan["pause"][0]["phase"] == "paused" and "paused: an invalid API key" in orch.plan_text(plan, events)
    clock.now = T0 + 900
    assert runner.plan([], [], dry=False)[1] == [] and host.wakes == [("ABC-9", orch.RETRY_TEXT)]


def test_a_stop_again_after_the_retry_cleared_pauses_at_once(tmp_path: Path) -> None:
    """a stop that follows a clear in the retry's window is the second stop, even when the session ran between."""
    runner, host, _, clock = runner_of(tmp_path)
    host.wakeable = True
    host.pause = RETRYABLE
    runner.plan([], [], dry=False)
    clock.now = T0 + 180
    runner.plan([], [], dry=False)
    host.pause = None
    clock.now = T0 + 200
    runner.plan([], [], dry=False)  # the retry cleared the stop
    host.pause = RETRYABLE
    clock.now = T0 + 240
    assert [e.kind for e in runner.plan([], [], dry=False)[1]] == ["paused"]
    assert host.wakes == [("ABC-9", orch.RETRY_TEXT)]


def test_a_cause_with_no_retry_pauses_at_once_and_sends_no_wake(tmp_path: Path) -> None:
    runner, host, _, clock = runner_of(tmp_path)
    host.pause = ("ABC-9", "the usage limit")  # the example's pair carries false
    plan, events = runner.plan([issue("ABC-3", area="docs")], [], dry=False)
    assert [e.kind for e in events] == ["paused"] and plan["pause"][0]["phase"] == "paused"
    assert host.wakes == [] and host.starts == []
    clock.now = T0 + 3600
    assert runner.plan([], [], dry=False)[1] == [] and host.wakes == []


def test_a_dry_run_sends_no_retry(tmp_path: Path) -> None:
    runner, host, _, clock = runner_of(tmp_path)
    host.pause = RETRYABLE
    clock.now = T0
    assert [e.kind for e in runner.plan([], [], dry=True)[1]] == ["retrying"]
    clock.now = T0 + 180
    assert runner.plan([], [], dry=True)[1] == [] and host.wakes == []
    assert runner.stops.pause == {None: RETRYABLE} and runner.stops.retry[None].sent is None


TWO_RETRIES = (("invalid api key", "an invalid API key", True), ("rate limit", "the rate or usage limit", True))


def test_a_changed_cause_before_the_retry_restarts_its_wait(tmp_path: Path) -> None:
    runner, host, _, clock = runner_of(tmp_path, stop_patterns=TWO_RETRIES)
    host.wakeable = True
    host.pause = RETRYABLE
    runner.plan([], [], dry=False)
    clock.now = T0 + 60
    host.pause = ("ABC-9", "the rate or usage limit")
    assert [(e.kind, e.reason) for e in runner.plan([], [], dry=False)[1]] == [("retrying", "the rate or usage limit")]
    assert runner.stops.retry[None].due == T0 + 60 + 180
    clock.now = T0 + 239
    assert runner.plan([], [], dry=False)[1] == [] and host.wakes == []
    clock.now = T0 + 240
    runner.plan([], [], dry=False)
    assert host.wakes == [("ABC-9", orch.RETRY_TEXT)]


def test_a_changed_key_gets_its_own_retry(tmp_path: Path) -> None:
    runner, host, _, clock = runner_of(tmp_path, stop_patterns=TWO_RETRIES)
    host.wakeable = True
    host.pause = RETRYABLE
    runner.plan([], [], dry=False)
    clock.now = T0 + 60
    host.pause = ("ABC-10", "an invalid API key")
    assert [(e.kind, e.key) for e in runner.plan([], [], dry=False)[1]] == [("retrying", "ABC-10")]
    clock.now = T0 + 240
    runner.plan([], [], dry=False)
    assert host.wakes == [("ABC-10", orch.RETRY_TEXT)]


def test_a_retry_the_session_cannot_take_pauses(tmp_path: Path) -> None:
    """A process that ended answers False to `wake`: the retry cannot clear the stop, so the loop pauses."""
    runner, host, _, clock = runner_of(tmp_path)
    host.pause = RETRYABLE  # `wakeable` is false: the wake answers False
    assert [e.kind for e in runner.plan([], [], dry=False)[1]] == ["retrying"]
    clock.now = T0 + 180
    assert [e.kind for e in runner.plan([], [], dry=False)[1]] == ["paused"]
    assert host.wakes == [("ABC-9", orch.RETRY_TEXT)]
    clock.now = T0 + 900
    assert runner.plan([], [], dry=False)[1] == [] and host.wakes == [("ABC-9", orch.RETRY_TEXT)]


def rerun_pr(*checks: dict[str, str]) -> dict[str, Any]:
    made = pr(7, "abc-7-x")
    made["statusCheckRollup"] = list(checks)
    return made


VERDICT_FAILED = {"name": "verdict", "status": "COMPLETED", "conclusion": "FAILURE", "startedAt": ASKED_AT, "completedAt": ASKED_AT}


def test_a_failed_wait_does_nothing_while_the_gates_rerun_runs(tmp_path: Path) -> None:
    """GitHub's rollup has one entry for each name: `web` runs again, `python` passed, the old `verdict` failed.
    No start and no escalation, not even at twice `claim_wait_seconds`; when the rerun fails, the ladder counts from it."""
    runner, host, writes, clock = runner_of(tmp_path)
    python = {"name": "python", "status": "COMPLETED", "conclusion": "SUCCESS", "startedAt": ASKED_AT, "completedAt": ASKED_AT}
    running = {"name": "web", "status": "IN_PROGRESS", "conclusion": "", "startedAt": "2026-11-02T10:05:00Z"}
    issues, prs = [waiting_issue()], [rerun_pr(python, running, VERDICT_FAILED)]
    clock.now = T0 + 2 * 300
    assert runner.fix_round(issues, prs, dry=False) == []
    assert host.starts == [] and writes.done == []
    failed_again = dict(running, status="COMPLETED", conclusion="FAILURE", completedAt="2026-11-02T10:12:00Z")
    prs = [rerun_pr(python, failed_again, VERDICT_FAILED)]
    clock.now = T0 + 12 * 60 + 299
    assert runner.fix_round(issues, prs, dry=False) == []
    clock.now = T0 + 12 * 60 + 300
    assert runner.fix_round(issues, prs, dry=False) == ["started a fix session for ABC-7 (claude-sonnet-5-5, medium)"]


def test_a_watchdog_rerun_that_started_inside_the_window_still_holds_after_it_starts(tmp_path: Path) -> None:
    runner, host, _, clock = runner_of(tmp_path)
    running = [{"name": n, "status": "QUEUED", "conclusion": "", "startedAt": "2026-11-02T10:20:00Z"} for n in ("python", "web")]
    issues, prs = [waiting_issue()], [rerun_pr(*running, VERDICT_FAILED)]
    clock.now = T0 + orch.GATES_RERUN_WINDOW + 600
    assert runner.fix_round(issues, prs, dry=False) == [] and host.starts == []


def test_a_failed_wait_with_no_gates_job_waits_for_the_watchdog_window(tmp_path: Path) -> None:
    runner, host, _, clock = runner_of(tmp_path)
    issues, prs = [waiting_issue()], [rerun_pr(VERDICT_FAILED)]
    clock.now = T0 + orch.GATES_RERUN_WINDOW - 1
    assert runner.fix_round(issues, prs, dry=False) == [] and host.starts == []
    clock.now = T0 + orch.GATES_RERUN_WINDOW
    assert runner.fix_round(issues, prs, dry=False) == ["started a fix session for ABC-7 (claude-sonnet-5-5, medium)"]


def test_a_failed_wait_with_every_required_check_passed_does_not_hold(tmp_path: Path) -> None:
    """A failed `verdict` is no gates case (OWL-30): passed gates after it leave no gap to wait for."""
    runner, _host, _, clock = runner_of(tmp_path)
    passed = [{"name": n, "status": "COMPLETED", "conclusion": "SUCCESS", "startedAt": ASKED_AT, "completedAt": "2026-11-02T10:20:00Z"}
              for n in ("python", "web")]
    issues, prs = [waiting_issue()], [rerun_pr(*passed, VERDICT_FAILED)]
    ended = orch._seconds("2026-11-02T10:20:00Z")
    assert ended is not None
    clock.now = ended + 1
    assert runner.fix_round(issues, prs, dry=False) == ["started a fix session for ABC-7 (claude-sonnet-5-5, medium)"]


def test_rerun_pending_is_only_for_failed_gates() -> None:
    assert not orch.rerun_pending(rerun_pr(VERDICT_FAILED), "changes requested", T0, T0, CFG)
    assert not orch.rerun_pending(failed_pr(7, "abc-7-x", ASKED_AT), "failed", T0, T0, CFG)  # a finished failed `python` is real


def test_a_failed_job_beside_a_first_attempt_that_still_runs_is_no_rerun() -> None:
    failed = {"name": "python", "status": "COMPLETED", "conclusion": "FAILURE", "startedAt": ASKED_AT, "completedAt": ASKED_AT}
    web = {"name": "web", "status": "IN_PROGRESS", "conclusion": "", "startedAt": ASKED_AT}
    assert not orch.rerun_pending(rerun_pr(failed, web, VERDICT_FAILED), "failed", T0, T0 + 3600, CFG)


# -- inference profiles ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "first", "item"), [("claude", ("claude-sonnet-5-5", "medium"), "claude-token"),
                                                      ("pi-ollama", ("pi-ollama/glm-5.3", "medium"), "ollama-key"),
                                                      ("pi-lithosai", ("pi-lithosai/deepseek-ai/DeepSeek-V4.1-Flash", "high"),
                                                       "lithosai-key")])
def test_each_profile_of_the_example_loads_with_its_cells_and_its_key_check(tmp_path: Path, name: str, first: tuple[str, str], item: str) -> None:
    cfg = orch.load_config(config_with(tmp_path, inference_profile=f'inference_profile = "{name}"', session_host='session_host = "paseo"'))
    assert cfg.inference_profile == name and cfg.run_cells[0] == first and len(cfg.run_cells) == 4
    assert f"agents-agent.{item} " in cfg.profile_key_check and " -w" not in cfg.profile_key_check  # never reads the value
    assert orch.run_settings(["needs-judgment", "intricate"], cfg) == (*cfg.run_cells[3], name)
    assert all(c.run_cells == cfg.run_cells for c in orch.team_configs(cfg))  # one profile for every team


def test_an_unknown_profile_or_a_profile_table_of_another_shape_stops_the_start(tmp_path: Path) -> None:
    with pytest.raises(orch.ConfigError, match=r"inference_profile 'openai' has no \[profiles.openai\] table: one of claude, pi-chatgpt"):
        orch.load_config(config_with(tmp_path, inference_profile='inference_profile = "openai"'))
    with pytest.raises(orch.ConfigError) as missing:  # the profile not in force is checked too: a switch is one line
        orch.load_config(config_with(tmp_path, key_check=None))
    assert "missing in" in str(missing.value) and "[profiles.claude] key_check = " in str(missing.value)
    for check in ('key_check = ""', "key_check = 3"):
        with pytest.raises(orch.ConfigError, match=r"\[profiles.claude\] key_check must be a command"):
            orch.load_config(config_with(tmp_path, key_check=check))
    path = tmp_path / "c.toml"
    path.write_text(TEST_TOML.replace("[profiles.pi-ollama]\n", "[profiles.pi-ollama]\nmodel = 'x'\n"))
    with pytest.raises(orch.ConfigError, match=r"unknown in .*\[profiles.pi-ollama\] model"):
        orch.load_config(path)
    path.write_text(TEST_TOML.replace("[profiles.pi-ollama]", "[profiles.'a b']"))
    with pytest.raises(orch.ConfigError, match="a profile's name"):
        orch.load_config(path)
    path.write_text(re.sub(r"(?ms)^\[profiles\.claude\].*?(?=^\[harnesses\.)", "", TEST_TOML))  # no profile at all
    with pytest.raises(orch.ConfigError, match=r"no \[profiles.NAME\] tables"):
        orch.load_config(path)


def test_a_config_made_before_the_profiles_names_the_move_of_its_cells(tmp_path: Path) -> None:
    path = tmp_path / "c.toml"
    path.write_text(TEST_TOML.replace('intricate_label = "intricate"\n', 'intricate_label = "intricate"\nneither = ["claude-sonnet-5-5", "medium"]\n'))
    with pytest.raises(orch.ConfigError, match=r"\[run_settings\] neither moved to the \[profiles.NAME\] tables: "):
        orch.load_config(path)


def test_the_example_s_herdr_command_gives_the_profile_to_the_session_and_a_start_passes_the_profile_in_force(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="prompt"):  # {prompt} with no skill_prompt
        orch.command_for(EXAMPLE.run_command, "ABC-7", "start", "glm-5.3", "high", token="20261120T155719Z-mini", profile="claude",
                         permission="auto")
    command = orch.command_for(EXAMPLE.run_command, "ABC-7", "start", "claude-sonnet-5-5", "high", token="20261120T155719Z-mini",
                               profile="claude", permission="auto", prompt=EXAMPLE.skill_prompt)
    assert command == ('claude --profile claude --permission-mode auto --model claude-sonnet-5-5 --effort high '
                       "'/implement ABC-7 start 20261120T155719Z-mini'")
    with pytest.raises(ValueError, match="profile"):
        orch.command_for("x {profile}", "ABC-7", "start", profile="a; rm -rf ~")
    runner, host, _, _ = runner_of(tmp_path, run_command="run-it {key} --profile {profile} {model}", inference_profile="pi-ollama",
                                   run_cells=(("pi-ollama/glm-5.3", "medium"),) * 4)
    runner.start_round([issue("ABC-7", area="docs")], [], dry=False)
    assert host.starts[0][1] == "run-it ABC-7 --profile pi-ollama pi-ollama/glm-5.3"


def test_check_names_the_profile_and_fails_when_its_key_is_not_in_the_keychain() -> None:
    assert orch.check_profile(CFG, lambda: True) == [(orch.OK, "inference profile: claude, its key is in the keychain")]
    [(level, text)] = orch.check_profile(CFG, lambda: False)
    assert level == orch.FAIL and "not in the keychain" in text and "agent_user.py user" in text

    def boom() -> bool:
        raise OSError("a reply's text")

    [(level, text)] = orch.check_profile(CFG, boom)
    assert level == orch.FAIL and "(OSError)" in text and "reply" not in text
    code, said = check(probes())
    assert code == 0 and "ok   inference profile: claude, its key is in the keychain" in said
    code, said = check(dataclasses.replace(probes(), profile_key=lambda: False))
    assert code == 1


@pytest.mark.parametrize(("profile", "pid", "started", "code", "line"), [
    ("claude", 123, "new", 0, "ok   Paseo daemon profile: claude"),
    ("pi-ollama", 123, "new", 0, "WARN Paseo daemon profile: pi-ollama, expected claude; the service switches it when no agent runs"),
    ("claude", 124, "new", 1, "FAIL Paseo daemon profile: cannot verify"),
    ("claude", 123, "old", 1, "FAIL Paseo daemon profile: cannot verify"),
    (None, 123, "new", 1, "FAIL Paseo daemon profile: cannot verify"),
])
def test_check_requires_the_current_paseo_daemon_to_use_the_configured_profile(tmp_path, profile, pid, started, code, line):
    cfg = orch.load_config(config_with(tmp_path, session_host="session_host = 'paseo'"))
    marker = tmp_path / ".local/state/orchestrator/paseo-profile.json"
    if profile is not None:
        marker.parent.mkdir(parents=True)
        marker.write_text(json.dumps({"profile": profile, "pid": pid, "startedAt": started}))

    def status(argv):
        assert argv == ["paseo", "daemon", "status", "--json"]
        return orch.subprocess.CompletedProcess(argv, 0, json.dumps({"localDaemon": "running", "pid": 123, "startedAt": "new"}))

    live = dataclasses.replace(probes(), paseo_profile=lambda: orch.read_paseo_profile(tmp_path, status))
    said = []
    assert orch.run_check(cfg, live, said.append) == code
    assert any(item.startswith(line) for item in said)
    if code:
        assert any("run `paseo daemon status --json`" in item for item in said)
    assert not any("paseo-daemon" in item for item in said), "the owner runs nothing as the agents' user"


def test_a_stopped_daemon_is_no_failure_since_the_service_starts_it(tmp_path: Path) -> None:
    """no daemon runs (after a restart of the Mac): an empty record, a WARN, and the service starts it."""
    def status(argv: list[str]) -> Any:
        return orch.subprocess.CompletedProcess(argv, 0, json.dumps({"localDaemon": "stopped"}))

    assert orch.read_paseo_profile(tmp_path, status) == ()
    assert orch.check_paseo_profile(("claude",), lambda: ()) == (
        orch.WARN, "Paseo daemon profile: no daemon runs; the service starts it on claude")


def test_read_paseo_profile_uses_the_agents_own_paseo_link(tmp_path: Path) -> None:
    """`--preflight` runs with a system PATH, so the record is read with the agent's link when it exists."""
    link = tmp_path / ".local/bin/paseo"
    link.parent.mkdir(parents=True)
    link.write_text("#!/bin/sh\n")
    marker = tmp_path / ".local/state/orchestrator/paseo-profile.json"
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({"profile": "claude", "pid": 123, "startedAt": "new"}))
    seen: list[list[str]] = []

    def status(argv: list[str]) -> Any:
        seen.append(list(argv))
        return orch.subprocess.CompletedProcess(argv, 0, json.dumps({"localDaemon": "running", "pid": 123, "startedAt": "new"}))

    assert orch.read_paseo_profile(tmp_path, status) == ("claude",)
    assert seen == [[str(link), "daemon", "status", "--json"]]


def test_the_preflight_at_a_start_and_at_the_install_names_the_daemon_profile_and_its_fix(
        tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`--preflight` (the check of `install`, `restart`, `set` and the starts) reads the daemon's profile, as
    `--check` does. A daemon on another profile is the service's switch, so it passes with a WARN, and
    `owlcode set inference_profile=NAME` is the whole switch."""
    path = config_with(tmp_path, session_host="session_host = 'paseo'", inference_profile="inference_profile = 'pi-lithosai'")
    live = dataclasses.replace(probes(), paseo_profile=lambda: ("pi-chatgpt",))
    for command in ("--preflight", "--check"):
        assert orch.main(["--config", str(path), command], probes=live) == 0
        said = capsys.readouterr().out
        assert ("WARN Paseo daemon profile: pi-chatgpt, expected pi-lithosai; the service switches it when no agent "
                "runs") in said and "paseo-daemon" not in said


def test_check_fails_when_pi_can_list_its_models_but_the_custom_provider_is_stale(tmp_path):
    cfg = orch.load_config(config_with(tmp_path, session_host="session_host = 'paseo'", inference_profile="inference_profile = 'pi-ollama'"))
    live = dataclasses.replace(probes(), paseo_profile=lambda: ("pi-ollama",), provider=lambda: False)
    said = []
    assert orch.run_check(cfg, live, said.append) == 1
    assert any(line.startswith("FAIL pi provider: pi-ollama does not match the catalog") for line in said)
    said = []
    orch.run_check(cfg, live, said.append)
    assert any("run owlcode admin to copy" in line for line in said), "the command takes the user from the config"


@pytest.mark.parametrize("code", [0, 1])
def test_the_real_probe_of_the_profile_s_key_runs_its_key_check_and_reads_the_exit_code(monkeypatch: pytest.MonkeyPatch, code: int) -> None:
    """the real `profile_key` probe splits `key_check` into an argument list and trusts only the exit code."""
    seen: list[list[str]] = []

    def fake(argv: Any) -> Any:
        seen.append(list(argv))
        return type("Done", (), {"returncode": code})()

    monkeypatch.setattr(orch, "run_quiet", fake)
    assert orch.real_probes(CFG).profile_key() is (code == 0)
    assert seen == [orch.shlex.split(CFG.profile_key_check)]


# -- the permission mode of each profile -----------------------------------------------------------------------


def test_the_example_names_a_permission_mode_for_each_profile_and_a_profile_without_one_stops_the_start(tmp_path: Path) -> None:
    cfg = orch.load_config(config_with(tmp_path, inference_profile='inference_profile = "claude"'))
    assert cfg.permission_mode == "auto"
    assert all(c.permission_mode == "auto" for c in orch.team_configs(cfg))
    path = tmp_path / "no-mode.toml"
    path.write_text(re.sub(r'(?m)^permission_mode = "auto".*\n', "", TEST_TOML))
    with pytest.raises(orch.ConfigError, match=r"\[profiles.claude\] permission_mode '' .* one of auto"):
        orch.load_config(path)
    for bad in ('"manual"', '"bypassPermissions"', '""', "true"):
        path.write_text(TEST_TOML.replace('permission_mode = "auto"', f"permission_mode = {bad}"))
        with pytest.raises(orch.ConfigError, match=r"\[profiles.claude\] permission_mode .* one of auto"):
            orch.load_config(path)


def test_claude_starts_its_session_in_auto_mode_in_each_host(tmp_path: Path) -> None:
    name, mode = "claude", "auto"
    herdr = orch.command_for(EXAMPLE.run_command, "ABC-7", "start", "m", "high", token="20261120T155719Z-mini", profile=name, permission=mode,
                             prompt=EXAMPLE.skill_prompt)
    assert f"--profile {name} --permission-mode {mode} --model m" in herdr
    paseo = orch.load_config(config_with(tmp_path, session_host='session_host = "paseo"', inference_profile=f'inference_profile = "{name}"'))
    assert f"--mode {mode}" in orch.check_command(paseo)[1]
    runner, host, _, _ = runner_of(tmp_path, run_command="run-it {key} --permission-mode {permission} {model}", inference_profile=name,
                                   permission_mode=mode, run_cells=(("m", "medium"),) * 4)
    runner.start_round([issue("ABC-7", area="docs")], [], dry=False)
    assert host.starts[0][1] == f"run-it ABC-7 --permission-mode {mode} m"


def test_a_permission_that_is_not_a_mode_never_reaches_a_shell() -> None:
    for bad in ("", "auto; rm -rf ~", "manual", "$(x)"):
        with pytest.raises(ValueError, match="permission mode"):
            orch.command_for("x {permission}", "ABC-7", "start", permission=bad)
    assert orch.command_for("x {permission}", "ABC-7", "start", permission="auto") == "x auto"
    assert orch.command_for("x", "ABC-7", "start") == "x"  # a template without the place needs none


def test_check_reports_the_permission_mode() -> None:
    code, said = check(probes())
    assert code == 0 and "ok   permission mode: auto (claude profile)" in said
    bypass = dataclasses.replace(CFG, inference_profile="ollama", permission_mode="bypassPermissions")
    assert orch.check_permission(bypass) == (orch.OK, "permission mode: bypassPermissions (ollama profile)")


# -- the settings of each harness ---------------------------------------------------------------------------

FAKE_HARNESS = """
[harnesses.pi]
skill_prompt = "run the skill implement for {key} with token {token}"
stop_patterns = [['quota exhausted', 'the quota']]
"""


def fake_config(tmp_path: Path) -> Path:
    """Use different prompt and stop settings for pi."""
    text = re.sub(r"(?ms)^\[harnesses\.pi\].*?(?=^\[linear\])", lambda _: FAKE_HARNESS.lstrip() + "\n", TEST_TOML)
    text = text.replace('inference_profile = "claude"', 'inference_profile = "pi-ollama"').replace('session_host = "herdr"', 'session_host = "paseo"')
    path = tmp_path / "fake.toml"
    path.write_text(text)
    return path


def test_the_config_loads_each_harness_table_and_refuses_a_profile_whose_harness_has_none(tmp_path: Path) -> None:
    cfg = orch.load_config(fake_config(tmp_path))
    assert (cfg.harness, cfg.skill_prompt, cfg.stop_patterns) == ("pi", "run the skill implement for {key} with token {token}",
                                                                  (("quota exhausted", "the quota", True),))
    path = tmp_path / "c.toml"
    # the profile not in force is checked too: a switch is one line
    path.write_text(re.sub(r"(?ms)^\[harnesses\.pi\].*?(?=^\[linear\])", "", TEST_TOML))
    with pytest.raises(orch.ConfigError, match=r"\[profiles.pi-ollama\] harness 'pi' needs a \[harnesses.pi\] table"):
        orch.load_config(path)
    path.write_text(re.sub(r'(?m)^harness = "claude"\n', "", TEST_TOML, count=1))
    with pytest.raises(orch.ConfigError) as missing:
        orch.load_config(path)
    assert '[profiles.claude] harness = "claude"' in str(missing.value)
    path.write_text(re.sub(r"(?ms)^\[harnesses\.claude\].*?(?=^\[linear\])", "", TEST_TOML))
    with pytest.raises(orch.ConfigError, match=r"no \[harnesses.NAME\] tables"):
        orch.load_config(path)
    path.write_text(re.sub(r"(?m)^skill_prompt = .*\n", "", TEST_TOML))
    with pytest.raises(orch.ConfigError) as missing:
        orch.load_config(path)
    assert '[harnesses.claude] skill_prompt = "/implement {key} start {token}"' in str(missing.value)
    path.write_text(TEST_TOML.replace("[harnesses.claude]\n", "[harnesses.claude]\nmodel = 'x'\n"))
    with pytest.raises(orch.ConfigError, match=r"unknown in .*\[harnesses.claude\] model"):
        orch.load_config(path)
    for prompt in ('"/implement"', '"/implement {key} {model}"', "3", '"/implement {key}\\nrm -rf ~"'):  # {key} only with {token}
        path.write_text(re.sub(r"(?m)^skill_prompt = .*$", lambda m, line=f"skill_prompt = {prompt}": line, TEST_TOML))
        with pytest.raises(orch.ConfigError, match=r"\[harnesses.claude\] skill_prompt must be"):
            orch.load_config(path)
    for stops in ("[['(', 'x']]", "[['x']]", "'x'", "[['x', '']]", "[['x', 'y', 'no']]"):
        path.write_text(re.sub(r"(?m)^stop_patterns = .*$", lambda m, line=f"stop_patterns = {stops}": line, TEST_TOML))
        with pytest.raises(orch.ConfigError, match=r"\[harnesses.claude\] stop_patterns"):
            orch.load_config(path)


def test_the_prompt_and_the_stop_patterns_come_from_the_harness_of_the_profile_in_force(tmp_path: Path) -> None:
    cfg = orch.load_config(fake_config(tmp_path))
    runner, host, _, _ = runner_of(tmp_path, run_command="run-it {key} {prompt}", skill_prompt=cfg.skill_prompt)
    token = runner.start_run("ABC-7", "start", "glm-5.3", "high")
    assert host.starts[0][1] == f"run-it ABC-7 'run the skill implement for ABC-7 with token {token}'"
    assert runner.token_line("glm-5.3", "high", token).endswith(f"(start {token})")  # the token reaches the session in the prompt
    fake_log = "[User] go\n[System Error] Quota exhausted for today\n"
    stopped = ("a1b2c3d4-0002", "ABC-9 ·", "error", "3 minutes ago")
    # the prompt fills only {key} and {token}, and goes into the shell as one quoted word
    with pytest.raises(ValueError, match="prompt"):
        orch.command_for("x {prompt}", "ABC-7", "start", model="m", prompt="/implement {key} {model}")
    assert orch.command_for("x {prompt}", "ABC-7", "start", prompt="go {key}; rm -rf ~") == "x 'go ABC-7; rm -rf ~'"
    host_of = orch.make_host(dataclasses.replace(cfg, session_host="paseo"))
    host_of.run = paseo_with(fake_log, stopped)
    assert host_of.paused() == {None: ("ABC-9", "the quota")}
    host_of.run = paseo_with(LIMIT_LOG, stopped)  # Claude Code's words are not this harness's
    assert host_of.paused() == {}
    claude = orch.make_host(dataclasses.replace(EXAMPLE, session_host="paseo"))
    claude.run = paseo_with(LIMIT_LOG, stopped)
    assert claude.paused() == {None: ("ABC-9", "the usage limit")}


@pytest.mark.parametrize(("old", "new", "error"), [
    ('[profiles.pi-ollama]', '[profiles.ollama]', r'remove \[profiles.ollama\]; use \[profiles.pi-ollama\] instead'),
    ('inference_profile = "claude"', 'inference_profile = "ollama"', 'set inference_profile = "pi-ollama"'),
    *[('deepseek-ai/DeepSeek-V4.1-Flash", "high', f'zai-org/GLM-5.3", "{alias}',
       f"neither effort '{alias}'.*one of low, high, max") for alias in ("minimal", "medium", "xhigh")],
    ('harness = "claude"', 'harness = "pi"', "harness 'pi'.*one of claude"),
    ('harness = "claude"', 'harness = "unknown"', "harness 'unknown'.*one of claude$"),
    ('provider = "anthropic"', 'provider = "ollama"', "provider 'ollama'.*one of anthropic"),
    ('[profiles.pi-ollama]\n', '[profiles.pi-ollama]\npermission_mode = "auto"\n', r"permission_mode 'auto'.*one of \(omit\)"),
    ('deepseek-ai/DeepSeek-V4.1-Flash", "high', 'deepseek-ai/DeepSeek-V4.1-Flash", "medium',
     "neither effort 'medium'.*one of low, high, max"),
    ('["glm-5.3", "medium"]', '["glm-5.3:cloud", "medium"]', "neither model 'glm-5.3:cloud'.*one of glm-5.3,"),
    ('[profiles.pi-ollama]\nprovider = "ollama"\nneither = ["glm-5.3", "medium"]',
     '[profiles.pi-ollama]\nprovider = "ollama"\nneither = ["ollama/glm-5.3", "medium"]',
     r'\[profiles.pi-ollama\] neither: write the model without "ollama/": glm-5\.3'),
    ('review_effort = "max"', 'review_effort = "extreme"', "review_effort 'extreme'.*one of low, high, max"),
    ('review_model = "zai-org/GLM-5.3-Flash"', 'review_model = "missing-model"',
     "review_model 'missing-model'.*one of deepseek-ai/"),
])
def test_model_catalog_rejects_invalid_profile_on_check_and_start(tmp_path, capsys, old, new, error):
    path = tmp_path / "invalid.toml"
    path.write_text(TEST_TOML.replace(old, new, 1))
    for command in ("--check", "--once"):
        assert orch.main(["--config", str(path), command]) == 2
        assert re.search(error, capsys.readouterr().err, re.MULTILINE)


@pytest.mark.parametrize("field", [*orch.CELLS, "review_model"])
@pytest.mark.parametrize(("profile", "prefix"), [("claude", "anthropic"), ("pi-chatgpt", "openai-codex"), ("pi-lithosai", "pi-lithosai")])
def test_model_fields_reject_provider_prefixes(tmp_path, capsys, field, profile, prefix):
    header = f"[profiles.{profile}]\n"
    before, after = TEST_TOML.split(header, 1)
    pattern = rf'(?m)^({field} = \[?")([^"\n]+)'
    match = re.search(pattern, after)
    assert match is not None
    model = match[2]
    path = tmp_path / "prefixed.toml"
    path.write_text(before + header + re.sub(pattern, rf'\g<1>{prefix}/\g<2>', after, count=1))
    for command in ("--check", "--preflight"):
        assert orch.main(["--config", str(path), command]) == 2
        assert f'[profiles.{profile}] {field}: write the model without "{prefix}/": {model}' in capsys.readouterr().err


def test_check_reports_a_catalog_without_required_sections(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(orch, "__file__", str(tmp_path / "orchestrator.py"))
    monkeypatch.setattr(orch, "_example_text", lambda: EXAMPLE_TEXT)
    (tmp_path / "model-catalog.json").write_text("{}")
    assert orch.main(["--config", str(example_file(tmp_path)), "--check"]) == 2
    assert "model-catalog.json: expected profiles, providers and efforts objects" in capsys.readouterr().err


def test_the_claude_profile_starts_its_command_and_reads_its_stops_as_before() -> None:
    """The example's `claude` harness holds the values that were constants before the harnesses became settings: the same argv, the same words."""
    token = "20261121T145508Z-mini.local"
    command = orch.command_for(EXAMPLE.run_command, "ABC-7", "start", "claude-opus-5-5", "high", token=token, profile="claude",
                               permission="auto", prompt=EXAMPLE.skill_prompt)
    before = ('claude --profile claude --permission-mode auto --model claude-opus-5-5 --effort high '
              f'"/implement ABC-7 start {token}"')
    assert shlex.split(command) == shlex.split(before)
    assert EXAMPLE.harness == "claude" and EXAMPLE.stop_patterns == (
        ("invalid api key", "an invalid API key", True), (r"usage limit|limit reached|hit your limit|\bresets\b", "the usage limit", False))
    assert EXAMPLE.inference_profile == "claude"


def test_a_profile_whose_harness_takes_no_effort_or_permission_mode_leaves_them_out(tmp_path: Path, monkeypatch) -> None:
    catalog = orch._model_catalog()
    catalog["efforts"]["configured"][""] = ""
    monkeypatch.setattr(orch, "_model_catalog", lambda: catalog)
    path = fake_config(tmp_path)
    text = re.sub(r'(?ms)(^\[profiles\.pi-ollama\]\n)(.*?)(?=^\[)',
                  lambda m: m[1] + re.sub(r'\["glm-5\.3", "(?:medium|high)"\]', '["glm-5.3"]', m[2]), path.read_text())
    path.write_text(text)
    cfg = orch.load_config(path)
    assert cfg.run_cells == (("pi-ollama/glm-5.3", ""),) * 4
    assert orch.run_line("glm-5.3", "") == "Run: glm-5.3, effort none"
    assert orch.check_permission(cfg) == (orch.OK, "permission mode: none (pi-ollama profile, harness pi)")
    command = orch.check_command(cfg)[1]
    assert "--provider pi --model pi-ollama/glm-5.3 'run the skill" in command
    assert " --thinking " not in command and " --mode " not in command


# The `[System Error]` lines of a pi agent in Paseo, measured 2026-11-21 on pi 1.5.2 and Paseo 0.16.2: the 401
# and 402 bodies are Ollama Cloud's own (https://ollama.com/v1), the 429 body is the scripted server's (not measured).
PI_STOPS = {
    "an invalid API key": '[System Error] 401: {"message":"Unauthorized","type":"api_error","param":null,"code":null} (stopReason=error, model=pi-ollama/glm-5.3)',
    "no credit left on the provider": ('[System Error] 402: {"message":"Add usage credits or subscribe and save up to 66%: https://ollama.com/plans '
                                       '(ref: 8d31bda0)","type":"api_error","param":null,"code":null} (stopReason=error, model=pi-ollama/glm-5.3)'),
    "the rate or usage limit": '[System Error] 429: {"message":"slow down","type":"api_error"} (stopReason=error, model=pi-ollama/glm-5.3)',
}


def test_the_pi_ollama_profile_starts_pi_s_skill_and_reads_pi_s_stops(tmp_path: Path) -> None:
    """Pi starts a skill with `/skill:NAME ARGS`. An ended pi process or Claude Code's words pause nothing."""
    path = config_with(tmp_path, inference_profile='inference_profile = "pi-ollama"', session_host='session_host = "paseo"')
    cfg = orch.load_config(path)
    assert (cfg.harness, cfg.permission_mode, cfg.skill_prompt) == ("pi", "", "/skill:implement {key} start {token}")
    assert cfg.credential == "keychain"
    assert cfg.run_cells == (("pi-ollama/glm-5.3", "medium"), ("pi-ollama/glm-5.3", "high"),
                             ("pi-ollama/glm-5.3", "medium"), ("pi-ollama/glm-5.3", "high"))
    started = orch.check_command(cfg)[1]
    assert "--model pi-ollama/glm-5.3 --thinking medium" in started
    assert shlex.split(started)[-1] == "/skill:implement EXAMPLE-1 start 20000101T000000Z-example"
    stopped = ("a1b2c3d4-0002", "ABC-9 ·", "error", "3 minutes ago")
    host = orch.make_host(dataclasses.replace(cfg, session_host="paseo"))
    for cause, line in PI_STOPS.items():
        host.run = paseo_with(f"[User] /skill:implement ABC-9\n{line}\n", stopped)
        assert host.paused() == {None: ("ABC-9", cause)}, line
    for other in (LIMIT_LOG, "[User] go\n[System Error] Pi process exited. Reload agent before sending another message.\n"):
        host.run = paseo_with(other, stopped)
        assert host.paused() == {}, other


PI_CHATGPT_STOPS = {
    # pi's own line, as pi 1.5.2 prints it: `openai-codex-responses.js` `parseErrorResponse` turns the body's
    # `usage_limit_reached` or a 429 into this sentence and throws it with no status and no code, so the pattern must match
    # the sentence
    "the ChatGPT plan's limit (the owner's own use of ChatGPT shares it)":
        "[System Error] You have hit your ChatGPT usage limit (plus plan). Try again in ~120 min. "
        "(stopReason=error, model=openai-codex/gpt-6.1-terra)",
    "the harness's sign-in (the owner signs pi in again)":
        "[System Error] OAuth refresh failed for openai-codex (stopReason=error, model=openai-codex/gpt-6.1-terra)",
}


def test_the_pi_chatgpt_profile_runs_on_pi_s_own_provider_with_no_key(tmp_path: Path) -> None:
    """the ChatGPT subscription is pi's own provider `openai-codex`, which signs itself in, so the profile has no
    keychain item. Loading the profile adds `openai-codex/` to its catalog IDs. A plan limit or a failed sign-in renewal pauses starts."""
    path = config_with(tmp_path, inference_profile='inference_profile = "pi-chatgpt"', session_host='session_host = "paseo"')
    cfg = orch.load_config(path)
    assert (cfg.harness, cfg.permission_mode, cfg.credential) == ("pi", "", "sign-in")
    assert cfg.run_cells == (("openai-codex/gpt-6.1-terra", "medium"), ("openai-codex/gpt-6.1-terra", "high"),
                             ("openai-codex/gpt-6.1-sol", "medium"), ("openai-codex/gpt-6.1-sol", "high"))
    assert cfg.profile_key_check == "/usr/bin/python3 -I /Users/agents/agent_user.py pi-signin --provider openai-codex"
    assert "--model openai-codex/gpt-6.1-terra --thinking medium" in orch.check_command(cfg)[1]
    stopped = ("a1b2c3d4-0002", "ABC-9 ·", "error", "3 minutes ago")
    host = orch.make_host(dataclasses.replace(cfg, session_host="paseo"))
    for cause, line in PI_CHATGPT_STOPS.items():
        host.run = paseo_with(f"[User] /skill:implement ABC-9\n{line}\n", stopped)
        assert host.paused() == {None: ("ABC-9", cause)}, line


PI_LITHOSAI_STOPS = {
    # LithosAI's own codes (docs.lithosai.com/rate-limits and /billing, read 2026-11-22): a 429 names the budget that
    # refused, and a 402 carries `insufficient_quota` at a zero balance. The status patterns covered these already; the
    # codes are there for a line that carries one without its status.
    "the rate or usage limit": '[System Error] 429: {"error":{"message":"slow down","code":"rate_limit_exceeded"}} '
                               "(stopReason=error, model=pi-lithosai/zai-org/GLM-5.3)",
    "no credit left on the provider": '[System Error] 402: {"error":{"message":"top up","code":"insufficient_quota"}} '
                                       "(stopReason=error, model=pi-lithosai/zai-org/GLM-5.3)",
}


def test_the_pi_lithosai_profile_runs_its_models_and_reads_lithosai_s_refusals(tmp_path: Path) -> None:
    """loading the profile adds `pi-lithosai/` to LithosAI's catalog IDs. A rate limit or an empty balance pauses starts."""
    path = config_with(tmp_path, inference_profile='inference_profile = "pi-lithosai"', session_host='session_host = "paseo"')
    cfg = orch.load_config(path)
    assert (cfg.harness, cfg.permission_mode, cfg.credential) == ("pi", "", "keychain")
    assert cfg.run_cells == (("pi-lithosai/deepseek-ai/DeepSeek-V4.1-Flash", "high"),) * 4
    assert "agents-agent.lithosai-key " in cfg.profile_key_check and " -w" not in cfg.profile_key_check
    assert "--model pi-lithosai/deepseek-ai/DeepSeek-V4.1-Flash --thinking high" in orch.check_command(cfg)[1]
    stopped = ("a1b2c3d4-0002", "ABC-9 ·", "error", "3 minutes ago")
    host = orch.make_host(dataclasses.replace(cfg, session_host="paseo"))
    for cause, line in PI_LITHOSAI_STOPS.items():
        host.run = paseo_with(f"[User] /skill:implement ABC-9\n{line}\n", stopped)
        assert host.paused() == {None: ("ABC-9", cause)}, line


def test_a_profile_that_signs_itself_in_checks_the_sign_in_and_says_so(tmp_path: Path) -> None:
    """`--check` runs the profile's `key_check` for a sign-in as for a key, and its line says what it found; the
    orchestrator never reads a token. A `credential` that is neither is a config error, named with the two values."""
    signed_in = dataclasses.replace(CFG, inference_profile="pi-chatgpt", credential="sign-in")
    assert orch.check_profile(signed_in, lambda: True) == [(orch.OK, "inference profile: pi-chatgpt, its sign-in is there")]
    [(level, text)] = orch.check_profile(signed_in, lambda: False)
    assert level == orch.FAIL and "sign-in is not set up" in text and "pi-signin" in text and "keychain" not in text
    path = tmp_path / "bad.toml"
    path.write_text(_with_lines(EXAMPLE_TEXT, {"credential": 'credential = "keyring"'}))
    with pytest.raises(orch.ConfigError, match="credential must be one of keychain, sign-in"):
        orch.load_config(path)


# -- a cell on another profile ------------------------------------------------------------------------------

LITHOS_CELL = '["deepseek-ai/DeepSeek-V4.1-Flash", "high"]'
MIXED = {"intricate": '["claude-opus-5-5", "high", "claude"]', "judgment": '["claude-opus-5-5", "medium", "claude"]',
         "both": '["claude-opus-5-5", "high", "claude"]'}


def mixed_config(tmp_path: Path, host: str = "paseo", profile: str = "pi-lithosai", claude_intricate: str = "", **cells: str) -> Path:
    """The tests' file with the cells of [profiles.pi-lithosai] replaced (the issue's mix by default), and with
    `claude_intricate` the `intricate` cell of [profiles.claude]."""
    text = _with_lines(TEST_TOML, {"inference_profile": f'inference_profile = "{profile}"', "session_host": f'session_host = "{host}"'})
    for cell, value in {**MIXED, **cells}.items():
        old = f"{cell} = {LITHOS_CELL}\n"
        assert text.count(old) == 1, cell
        text = text.replace(old, f"{cell} = {value}\n")
    if claude_intricate:
        text, n = re.subn(r'^intricate = \["claude-sonnet-5-5", "high"\].*$', f"intricate = {claude_intricate}", text, flags=re.MULTILINE)
        assert n == 1
    path = tmp_path / "mixed.toml"
    path.write_text(text)
    return path


def test_a_cell_runs_on_the_profile_it_names_and_the_review_stays_the_profile_in_force(tmp_path: Path) -> None:
    cfg = orch.load_config(mixed_config(tmp_path))
    assert orch.run_settings([], cfg) == ("pi-lithosai/deepseek-ai/DeepSeek-V4.1-Flash", "high", "pi-lithosai")
    assert orch.run_settings(["intricate"], cfg) == ("claude-opus-5-5", "high", "claude")  # no pi prefix on Claude Code
    assert orch.run_settings(["needs-judgment"], cfg) == ("claude-opus-5-5", "medium", "claude")
    assert orch.run_settings(["needs-judgment", "intricate"], cfg) == ("claude-opus-5-5", "high", "claude")
    assert cfg.review == ("pi-lithosai/zai-org/GLM-5.3-Flash", "max")
    assert [name for name, _ in cfg.cell_runs] == ["claude"] and dict(cfg.cell_runs)["claude"].harness[0] == "claude"
    claude = orch.cell_config(cfg, "claude", "high")
    assert (claude.inference_profile, claude.harness, claude.permission_mode, claude.review) == ("claude", "claude", "auto", cfg.review)
    assert orch.cell_config(cfg, "pi-lithosai", "high") is cfg
    # A table with no third field has no cell on another profile: the config is as before.
    plain = orch.load_config(config_with(tmp_path, inference_profile='inference_profile = "pi-lithosai"', session_host='session_host = "paseo"'))
    assert plain.cell_profiles == () and plain.cell_runs == ()
    # [model, PROFILE] is the form of a harness with no effort level: the second field is a profile when it names one, so
    # this cell has no effort level beside the other cells on claude.
    with pytest.raises(orch.ConfigError, match=r"\[profiles.pi-lithosai\]: every cell on one profile names an effort level"):
        orch.load_config(mixed_config(tmp_path, intricate='["claude-opus-5-5", "claude"]'))


def test_a_mixed_start_gives_the_session_the_review_of_the_profile_in_force(tmp_path: Path) -> None:
    """a cell on another profile starts `session --profile CELL`, and that session reviews on the profile in force."""
    cfg = dataclasses.replace(orch.load_config(mixed_config(tmp_path)), session_host="herdr")
    token = "20261123T114900Z-mini"
    mixed = orch.format_run(cfg, "ABC-7", "start", "claude-opus-5-5", "high", "ABC-7 ·", "/a", token, "claude")
    assert "--profile claude --review-profile pi-lithosai --workers 7 --permission-mode" in mixed
    # A cell on the profile in force, and a start with no cell profile, keep the command as before.
    for profile in ("pi-lithosai", ""):
        assert "--review-profile" not in orch.format_run(cfg, "ABC-7", "start", "m", "high", "ABC-7 ·", "/a", token, profile)
    with pytest.raises(ValueError):
        orch.command_for(orch.RUN_TEMPLATES[("herdr", "claude")], "ABC-7", "start", "m", "high", profile="claude",
                         permission="auto", prompt="go", review_profile="x; rm -rf ~")


@pytest.mark.parametrize(("cell", "message"), [
    ('["claude-opus-5-5", "high", "openai"]', r"\[profiles.pi-lithosai\] intricate: 'openai' names no \[profiles.openai\] table"),
    ('["claude-opus-5-5", "high", "pi-lithosai"]', r"\[profiles.pi-lithosai\] intricate names its own table 'pi-lithosai'"),
    ('["deepseek-ai/DeepSeek-V4.1-Flash", "high", "claude"]',
     r"\[profiles.pi-lithosai\] \(on \[profiles.claude\]\) intricate model 'deepseek-ai/DeepSeek-V4.1-Flash' is not in the model catalog"),
    ('["claude-opus-5-5", "minimal", "claude"]', r"\(on \[profiles.claude\]\) intricate effort 'minimal' is not in the model catalog"),
])
def test_a_third_field_of_no_table_or_a_model_its_provider_lacks_stops_the_start(tmp_path: Path, capsys: pytest.CaptureFixture[str],
                                                                                 cell: str, message: str) -> None:
    path = mixed_config(tmp_path, intricate=cell)
    with pytest.raises(orch.ConfigError, match=message):
        orch.load_config(path)
    assert orch.main(["--config", str(path), "--check"], probes=probes()) == 2
    assert re.search(message, capsys.readouterr().err)


def test_a_start_builds_its_command_from_the_cells_profile_and_names_it_on_the_run_line(tmp_path: Path) -> None:
    cfg = dataclasses.replace(orch.load_config(mixed_config(tmp_path)), run_cwd=str(tmp_path))
    host, writes = FakeHost(), FakeWrites()
    runner = orch.Runner(cfg, host, writes.writes(), Clock())
    runner.host_name = "mini"
    hard, easy = issue("ABC-7", area="docs", labels=("intricate",)), issue("ABC-8", area="infra")
    assert runner.start_round([hard, easy], [], dry=False) == ["started ABC-7 (claude-opus-5-5, high)",
                                                              "started ABC-8 (pi-lithosai/deepseek-ai/DeepSeek-V4.1-Flash, high)"]
    [(_, claude, _), (_, pi, _)] = host.starts
    assert " --provider claude --model claude-opus-5-5 --thinking high --mode auto '/implement ABC-7 start 20261102T100000Z-mini'" in claude
    assert " --provider pi --model pi-lithosai/deepseek-ai/DeepSeek-V4.1-Flash --thinking high '/skill:implement ABC-8 start " in pi
    assert [body for _, _, body in writes.done] == [
        "Run: claude-opus-5-5, effort high; review pi-lithosai/zai-org/GLM-5.3-Flash, effort max; profile claude; "
        "pytest workers 7 (start 20261102T100000Z-mini)",
        "Run: pi-lithosai/deepseek-ai/DeepSeek-V4.1-Flash, effort high; review pi-lithosai/zai-org/GLM-5.3-Flash, effort max; "
        "profile pi-lithosai; pytest workers 7 (start 20261102T100000Z-mini)"]
    assert orch.fix_run_line("m", "high", ("r", "max"), 7, "claude") == "Run: m, effort high; review r, effort max; profile claude (fix round of PR #7)"
    assert orch.run_line("m", "high", ("r", "max"), "tok") == "Run: m, effort high; review r, effort max (start tok)"  # no profile: as before


def test_the_preflight_checks_each_used_profiles_credential_and_refuses_a_mix_the_host_cannot_run(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = orch.load_config(mixed_config(tmp_path))
    asked: list[str] = []

    def key(name: str) -> bool:
        asked.append(name)
        return name != "claude"

    lines = orch.preflight(cfg, lambda: True, lambda _: True, lambda: ("pi-lithosai", "claude"), key)
    assert asked == ["claude"]
    assert lines[0] == (orch.OK, "inference profile: pi-lithosai, its key is in the keychain")
    assert lines[1][0] == orch.FAIL and lines[1][1].startswith("cell profile: claude (intricate, judgment, both), its key is not in the keychain")
    assert lines[2] == (orch.OK, "Paseo daemon profiles: pi-lithosai, claude"), "Paseo runs the mix now: no refusal of it"
    # The failed key of claude owns its line and halts only its cells: the other profile's models are still checked.
    assert lines[1].profiles == {"claude"} and lines[0].profiles == {"pi-lithosai"} and lines[2].profiles is None
    assert all(c.profiles == {"pi-lithosai"} and "pi-lithosai" in c.text for c in lines[3:]) and len(lines) > 3
    assert (orch.OK, "cell profile: claude (intricate, judgment, both), its key is in the keychain") in orch.preflight(
        cfg, lambda: True, lambda _: True, lambda: ("pi-lithosai", "claude"), lambda _: True)
    # The plain process starts only Claude Code: a cell on a pi profile fails, one on a Claude Code profile passes. herdr runs
    # a pi cell too, with the mix's review: the session's `pi` alias takes `--review-profile` as `session` does.
    herdr = orch.load_config(mixed_config(tmp_path, "herdr", "claude", '["deepseek-ai/DeepSeek-V4.1-Flash", "high", "pi-lithosai"]'))
    assert orch.check_mix(herdr) == []
    pi_start = orch.format_run(herdr, "ABC-7", "start", "pi-lithosai/deepseek-ai/DeepSeek-V4.1-Flash", "high", "ABC-7 ·", "/a",
                               "20261123T114900Z-mini", "pi-lithosai")
    assert pi_start == ("pi --profile pi-lithosai --review-profile claude --workers 7 --model pi-lithosai/deepseek-ai/DeepSeek-V4.1-Flash "
                        "--thinking high '/skill:implement ABC-7 start 20261123T114900Z-mini'")
    for host in ("process",):
        on_claude = orch.load_config(mixed_config(tmp_path, host, "claude", '["deepseek-ai/DeepSeek-V4.1-Flash", "high", "pi-lithosai"]'))
        assert orch.run_settings(["intricate"], on_claude) == ("pi-lithosai/deepseek-ai/DeepSeek-V4.1-Flash", "high", "pi-lithosai")
        assert (orch.FAIL, f"cell profile: pi-lithosai (intricate) runs on pi, and {host} starts only claude: each cell "
                           "must use a profile of that harness") in orch.check_mix(on_claude)
        assert orch.check_command(on_claude)[0] == orch.OK  # the first cell runs on claude
    # Paseo runs the mix: pi lists each cell's model on a pi profile, and the review's of a pi profile in force.
    checked: list[str] = []
    lines = orch.preflight(cfg, lambda: True, lambda model: checked.append(model) is None, lambda: ("pi-lithosai", "claude"), lambda _: True)
    assert checked == ["pi-lithosai/deepseek-ai/DeepSeek-V4.1-Flash", "pi-lithosai/zai-org/GLM-5.3-Flash"]
    assert (orch.OK, "claude: credential check only (no offline model lookup)") in lines
    assert (orch.OK, "pi-lithosai neither: pi-lithosai/deepseek-ai/DeepSeek-V4.1-Flash is available") in lines


def test_check_lists_each_used_profile(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = mixed_config(tmp_path)
    both = dataclasses.replace(probes(), paseo_profile=lambda: ("pi-lithosai", "claude"))
    assert orch.main(["--config", str(path), "--check"], probes=both) == 0
    out = capsys.readouterr().out
    assert "ok   inference profile: pi-lithosai, its key is in the keychain\n" in out
    assert "ok   cell profile: claude (intricate, judgment, both), its key is in the keychain\n" in out
    assert "ok   Paseo daemon profiles: pi-lithosai, claude\n" in out  # the issue's line
    only = dataclasses.replace(probes(), paseo_profile=lambda: ("pi-lithosai",))  # a daemon from before the mix
    assert orch.main(["--config", str(path), "--check"], probes=only) == 0
    assert ("WARN Paseo daemon profiles: pi-lithosai, expected pi-lithosai and claude; the service switches it when no agent "
            "runs\n") in capsys.readouterr().out


def test_cell_key_runs_the_named_profiles_key_check_and_the_example_command_fails_on_a_harness_the_host_lacks(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = orch.load_config(mixed_config(tmp_path))
    ran: list[list[str]] = []

    def run(argv: list[str]) -> Any:
        ran.append(argv)
        return orch.subprocess.CompletedProcess(argv, 0 if len(ran) == 1 else 44, "", "")

    monkeypatch.setattr(orch, "run_quiet", run)
    assert orch.cell_key(cfg, "claude") and not orch.real_probes(cfg).cell_key("claude")
    assert ran[0] == ran[1] and "agents-agent.claude-token" in ran[0]  # the claude table's key_check, not the one in force
    # The first cell on a pi profile, with a host that has no command for pi: the line fails and names why.
    on_process = orch.load_config(mixed_config(tmp_path, "process", "claude", '["deepseek-ai/DeepSeek-V4.1-Flash", "high", "pi-lithosai"]'))
    on_process = dataclasses.replace(on_process, cell_profiles=("pi-lithosai", *on_process.cell_profiles[1:]))  # `neither` on pi too
    level, text = orch.check_command(on_process)
    assert level == orch.FAIL and text.startswith("example start command: [profiles.pi-lithosai]: unsupported session host/harness pair process/pi")


# -- Paseo's daemon holds the keys of every profile that the cells use ---------------------------------------------


def test_a_paseo_agent_empties_the_key_variables_of_the_daemons_other_profiles_and_holds_no_key_value(tmp_path: Path) -> None:
    """The daemon's environment holds each profile's key, so each agent's start command empties the others' (`--env VAR=`,
    measured: Claude Code and pi both read an empty variable as a missing key). The command holds a name and no value."""
    cfg = orch.load_config(mixed_config(tmp_path))
    assert orch.daemon_profiles(cfg) == ("pi-lithosai", "claude")
    assert orch.hidden_keys(cfg, "claude") == ("LITHOSAI_API_KEY", "OLLAMA_API_KEY"), "the pi-ollama key too: no cell names it"
    assert orch.hidden_keys(cfg, "pi-lithosai") == ("CLAUDE_CODE_OAUTH_TOKEN", "OLLAMA_API_KEY")
    pi_run = orch.format_run(cfg, "ABC-7", "start", *orch.run_settings([], cfg)[:2], "ABC-7 ·", "/clone", "20261123T100000Z-mini", "pi-lithosai")
    claude_run = orch.format_run(cfg, "ABC-7", "start", "claude-opus-5-5", "high", "ABC-7 ·", "/clone", "20261123T100000Z-mini", "claude")
    assert " --label orchestrator=ABC-7 --label profile=pi-lithosai --env CLAUDE_CODE_OAUTH_TOKEN= --env OLLAMA_API_KEY= --env PYTEST_XDIST_AUTO_NUM_WORKERS=7 --provider pi " in pi_run
    assert " --label orchestrator=ABC-7 --label profile=claude --env LITHOSAI_API_KEY= --env OLLAMA_API_KEY= --env PYTEST_XDIST_AUTO_NUM_WORKERS=7 --provider claude " in claude_run
    for command in (pi_run, claude_run):
        assert re.findall(r"--env (\S+)", command) == [m for m in re.findall(r"--env (\S+)", command) if re.fullmatch(r"[A-Z_]+=|PYTEST_XDIST_AUTO_NUM_WORKERS=[0-9]+", m)]
    # the paseo host adds the workspace after `paseo run` and keeps the flags
    assert shlex.split(claude_run)[:2] == ["paseo", "run"]
    # no mix, no flag: the command is the one of before; herdr and the plain process never get one
    plain = orch.load_config(config_with(tmp_path, session_host="session_host = 'paseo'"))
    # no mix: the daemon may hold more than the cells name now, so every other profile's key is emptied all the same
    assert orch.hidden_keys(plain, "claude") == ("LITHOSAI_API_KEY", "OLLAMA_API_KEY")
    assert "--env LITHOSAI_API_KEY= --env OLLAMA_API_KEY= --env PYTEST_XDIST_AUTO_NUM_WORKERS=7 --provider claude" in orch.check_command(plain)[1]
    herdr = orch.load_config(mixed_config(tmp_path, "herdr", "claude", '["deepseek-ai/DeepSeek-V4.1-Flash", "high", "pi-lithosai"]'))
    assert "--env" not in orch.format_run(herdr, "ABC-7", "start", "m", "high", "ABC-7 ·", "/clone", "20261123T100000Z-mini", "claude")
    with pytest.raises(ValueError, match="variable"):
        orch.command_for(orch.RUN_TEMPLATES[("paseo", "pi")], "ABC-7", "start", "m", "high", "ABC-7 ·", "/clone", "20261123T100000Z-m",
                         "pi-lithosai", prompt="x", hide=("A; rm -rf ~",))
    # `--check` shows the command that a start runs, for the cell that has no label: the first cell, on pi
    assert "--env CLAUDE_CODE_OAUTH_TOKEN= --env OLLAMA_API_KEY= --env PYTEST_XDIST_AUTO_NUM_WORKERS=7 --provider pi" in orch.check_command(cfg)[1]


@pytest.mark.parametrize(("log", "pi_cause", "claude_cause"), [
    ('[System Error] 402: {"message":"Add usage credits"}\n', "no credit left on the provider", None),
    ("[System Error] You've hit your limit · resets 3pm\n", None, "the usage limit"),
])
def test_the_paseo_host_matches_a_stopped_agents_log_against_its_own_harnesss_patterns(log: str, pi_cause: str | None,
                                                                                    claude_cause: str | None, tmp_path: Path) -> None:
    """With two harnesses a pi agent's error went through Claude Code's patterns and paused nothing: the part of
    the agent's Paseo `provider` before the first `/` (the harness's name) picks the patterns."""
    cfg = orch.load_config(mixed_config(tmp_path))
    assert set(orch.harness_stops(cfg)) == {"pi", "claude"}
    for provider, cause in (("pi", pi_cause), ("claude", claude_cause)):
        fake = FakePaseo()
        fake.replies["ls"] = json.dumps([{"id": "a1b2c3d4-0002", "shortId": "a1b2c4", "name": "ABC-9 ·", "provider": provider,
                                          "status": "error", "created": "3 minutes ago"}])
        fake.replies["logs"] = log
        host = orch.make_host(cfg)
        assert isinstance(host, orch.PaseoHost)
        host.run = fake
        assert host.paused() == ({None: ("ABC-9", cause)} if cause else {}), provider


def test_a_retry_follows_the_pair_of_any_harness_that_a_cell_runs_on(tmp_path: Path) -> None:
    """The usage limit of Claude Code gets no retry, also when the profile in force is a pi profile."""
    cfg = orch.load_config(mixed_config(tmp_path))
    assert cfg.harness == "pi" and not orch.Stops.retry_allowed(cfg, "the usage limit")
    assert not orch.Stops.retry_allowed(cfg, "no credit left on the provider") and orch.Stops.retry_allowed(cfg, "an invalid API key")
    alone = orch.load_config(config_with(tmp_path, session_host="session_host = 'paseo'",
                                          inference_profile="inference_profile = 'pi-lithosai'"))
    assert orch.Stops.retry_allowed(alone, "the usage limit"), "no mix: a cause of no pair of the harness gets the retry, as before"


@pytest.mark.parametrize(("marker", "expected"), [
    ({"profile": "pi-lithosai", "profiles": ["pi-lithosai", "claude"]}, ("pi-lithosai", "claude")),
    ({"profile": "claude"}, ("claude",)),  # a record of a daemon that an older script started
    ({"profile": "claude", "profiles": ["claude", "gone"]}, None),
    ({"profile": "claude", "profiles": []}, None),
    ({"profile": "claude", "profiles": "claude"}, None),
])
def test_the_daemons_record_holds_the_set_of_profiles(tmp_path: Path, marker: dict[str, Any], expected: tuple[str, ...] | None) -> None:
    path = tmp_path / ".local/state/orchestrator/paseo-profile.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({**marker, "pid": 123, "startedAt": "new"}))

    def status(argv: list[str]) -> Any:
        return orch.subprocess.CompletedProcess(argv, 0, json.dumps({"localDaemon": "running", "pid": 123, "startedAt": "new"}))

    assert orch.read_paseo_profile(tmp_path, status) == expected


def test_the_daemon_passes_when_it_holds_every_profile_of_the_cells_and_the_review_of_the_profile_in_force() -> None:
    want = ("pi-lithosai", "claude")
    assert orch.check_paseo_profile(want, lambda: ("pi-lithosai", "claude")) == (orch.OK, "Paseo daemon profiles: pi-lithosai, claude")
    assert orch.check_paseo_profile(want, lambda: ("pi-lithosai", "claude", "pi-ollama"))[0] == orch.OK, "more keys than needed"
    switch = "; the service switches it when no agent runs"
    assert orch.check_paseo_profile(want, lambda: ("pi-lithosai",), "agents") == (
        orch.WARN, f"Paseo daemon profiles: pi-lithosai, expected pi-lithosai and claude{switch}")
    # the keys are all there, but the daemon's local review is another profile's
    level, line = orch.check_paseo_profile(want, lambda: ("claude", "pi-lithosai"))
    assert level == orch.WARN and "claude, pi-lithosai, expected pi-lithosai first (its local review)" in line
    assert orch.check_paseo_profile(("claude", "pi-ollama", "pi-lithosai"), lambda: ("claude",))[1] == (
        f"Paseo daemon profiles: claude, expected claude, pi-ollama and pi-lithosai{switch}")
    level, line = orch.check_paseo_profile(want, lambda: None, "agents")
    assert level == orch.FAIL and line.startswith("Paseo daemon profiles: cannot verify the running daemon: as agents, run")


def test_a_profile_on_its_harnesss_own_sign_in_has_no_key_variable_to_empty(tmp_path: Path) -> None:
    """pi-chatgpt has no key: no variable is emptied for it, and its agents empty the keys of the others."""
    cfg = orch.load_config(mixed_config(tmp_path, judgment='["gpt-6.1-sol", "medium", "pi-chatgpt"]'))
    assert orch.daemon_profiles(cfg) == ("pi-lithosai", "claude", "pi-chatgpt")
    assert orch.key_variable("pi-chatgpt") == "" and orch.key_variable("gone") == ""
    assert orch.hidden_keys(cfg, "pi-chatgpt") == ("CLAUDE_CODE_OAUTH_TOKEN", "LITHOSAI_API_KEY", "OLLAMA_API_KEY")
    assert "" not in orch.hidden_keys(cfg, "claude"), "no empty name for the profile that has no key"


def test_a_daemon_that_holds_a_profile_no_cell_names_still_has_its_key_emptied_in_every_agent(tmp_path: Path) -> None:
    """The cells drop pi-ollama after the daemon started with it, the preflight passes that daemon (it holds
    every profile of the cells), and the start command still empties the pi-ollama key."""
    cfg = orch.load_config(mixed_config(tmp_path))
    assert "pi-ollama" not in orch.daemon_profiles(cfg)
    assert orch.check_paseo_profile(orch.daemon_profiles(cfg), lambda: ("pi-lithosai", "claude", "pi-ollama"))[0] == orch.OK
    command = orch.format_run(cfg, "ABC-7", "start", "claude-opus-5-5", "high", "ABC-7 ·", "/clone", "20261123T100000Z-mini", "claude")
    assert " --env OLLAMA_API_KEY=" in command and " --env LITHOSAI_API_KEY=" in command
    assert " --env CLAUDE_CODE_OAUTH_TOKEN=" not in command, "an agent keeps its own profile's key"


# -- A stop or a failed setup check of one profile holds only the cells on that profile ------------------------------

LITHOS_STOP = ("ABC-1", "no credit left on the provider")  # a 402 of LithosAI: a cause with no retry
PAUSED_LITHOS = "paused (pi-lithosai): no credit left on the provider (the newest session, of ABC-1, stopped on an API error)"


def mixed_runner(tmp_path: Path) -> tuple[Any, FakeHost, FakeWrites, Clock]:
    """The runner of the issue's mix: the cells on pi-lithosai (the profile in force), `intricate` on claude."""
    cfg = dataclasses.replace(orch.load_config(mixed_config(tmp_path)), run_cwd=str(tmp_path))
    host, writes, clock = FakeHost(), FakeWrites(), Clock()
    runner = orch.Runner(cfg, host, writes.writes(), clock)
    runner.host_name = "mini"
    return runner, host, writes, clock


def test_with_no_cell_on_another_profile_a_stop_with_a_profile_label_holds_every_start_as_before(tmp_path: Path) -> None:
    """The first case: one profile in force. The label `profile=claude` that every start now gives changes
    nothing: the stop holds the whole loop, the lines name no profile, and no issue gets a line of its own."""
    runner, host, writes, _ = runner_of(tmp_path)
    host.wakeable = True
    host.pauses = {"claude": ("ABC-9", "the usage limit")}
    issues, prs = [waiting_issue(), issue("ABC-3", area="docs")], [pr(7, "abc-7-x", reviews=ASKED)]
    for at in (T0 + 60, T0 + 900):
        runner.clock.now = at  # type: ignore[attr-defined]
        plan, events = runner.plan(issues, prs, dry=False)
    assert [e.text for e in runner.plan(issues, prs, dry=False)[1]] == []
    assert plan["pause"] == [{"key": "ABC-9", "cause": "the usage limit", "phase": "paused"}], "no `profile` key without a mix"
    assert "paused: the usage limit" in orch.plan_text(plan, events) and "(claude)" not in "".join(orch.plan_text(plan, events))
    assert host.starts == [] and host.wakes == [] and writes.done == []
    assert runner.start_round(issues, prs, dry=False) == [], "a start held by the stop has no line for its issue"
    only = orch.Runner(fix_cfg(tmp_path), FakeHost(), None, Clock())
    only.host.pauses = {"claude": ("ABC-9", "the usage limit")}  # type: ignore[attr-defined]
    assert only.plan([], [], dry=True)[1][0].text == ("paused: the usage limit (the newest session, of ABC-9, stopped on an API error): "
                                                     "no start, wake or escalation until the owner sends to that agent or archives it")


def test_a_stop_of_one_profile_pauses_its_cells_and_the_other_cells_start(tmp_path: Path) -> None:
    """The second profile's agent stopped on a 402, an `intricate` issue still starts on claude."""
    runner, host, writes, _ = mixed_runner(tmp_path)
    host.pauses = {"pi-lithosai": LITHOS_STOP}
    plain, hard = issue("ABC-2", area="docs"), issue("ABC-3", area="infra", labels=("intricate",))
    plan, events = runner.plan([plain, hard], [], dry=False)
    assert PAUSED_LITHOS in orch.plan_text(plan, events)
    assert plan["pause"] == [{"profile": "pi-lithosai", "key": "ABC-1", "cause": LITHOS_STOP[1], "phase": "paused"}]
    assert [e.text for e in events if e.kind == "started"] == ["started ABC-3 (claude-opus-5-5, high)"]
    assert plan["other_waits"] == [{"key": "ABC-2", "reason": "its cell runs on pi-lithosai, which is paused"}]
    assert [label for label, _, _ in host.starts] == ["ABC-3 ·"]
    assert " --label profile=claude " in host.starts[0][1] and " --provider claude " in host.starts[0][1]
    assert [e.text for e in events if e.kind == "paused"] == [
        "paused (pi-lithosai): no credit left on the provider (the newest session, of ABC-1, stopped on an API error): no start, "
        "wake or escalation of a cell on that profile until the owner sends to that agent or archives it"]
    # the line of the log
    runner.clock.now = T0 + 60  # type: ignore[attr-defined]
    assert runner.start_round([plain], [], dry=False) == ["ABC-2 waits: its cell runs on pi-lithosai, which is paused"]
    host.pauses = {}  # the owner archived the stopped agent: the issue starts on its cell
    events = runner.plan([plain], [], dry=False)[1]
    assert [e.text for e in events] == ["resumed (pi-lithosai): the newest session no longer shows no credit left on the provider",
                                        "started ABC-2 (pi-lithosai/deepseek-ai/DeepSeek-V4.1-Flash, high)"]
    assert len(writes.done) == 2  # one run line for each start


def test_a_stop_of_the_other_profile_holds_the_intricate_issues_only(tmp_path: Path) -> None:
    runner, host, _, _ = mixed_runner(tmp_path)
    host.pauses = {"claude": ("ABC-5", "the usage limit")}
    plain, hard = issue("ABC-2", area="docs"), issue("ABC-3", area="infra", labels=("intricate",))
    plan, events = runner.plan([plain, hard], [], dry=False)
    assert [e.text for e in events if e.kind == "started"] == ["started ABC-2 (pi-lithosai/deepseek-ai/DeepSeek-V4.1-Flash, high)"]
    assert plan["other_waits"] == [{"key": "ABC-3", "reason": "its cell runs on claude, which is paused"}]
    assert "paused (claude): the usage limit (the newest session, of ABC-5, stopped on an API error)" in orch.plan_text(plan, events)


def test_a_stop_with_no_profile_label_pauses_every_profile(tmp_path: Path) -> None:
    """A session that no start of this orchestrator labelled (the owner's, or one from before the label): it stands for all."""
    runner, host, writes, _ = mixed_runner(tmp_path)
    host.pause = ("ABC-9", "the usage limit")
    issues = [issue("ABC-2", area="docs"), issue("ABC-3", area="infra", labels=("intricate",))]
    plan, events = runner.plan(issues, [], dry=False)
    assert host.starts == [] and writes.done == [] and plan["other_waits"] == []
    assert "paused (no profile label): the usage limit (the newest session, of ABC-9, stopped on an API error)" in orch.plan_text(plan, events)
    assert plan["pause"][0]["profile"] is None


def test_each_profile_has_its_own_retry_and_window(tmp_path: Path) -> None:
    """The rules for each profile: one `Retry` and one window, so one profile's retry, clear or pause never moves the other's."""
    runner, host, _, clock = mixed_runner(tmp_path)
    host.wakeable = True
    host.pauses = {"pi-lithosai": ("ABC-1", "an invalid API key"), "claude": ("ABC-5", "an invalid API key")}
    events = runner.plan([], [], dry=False)[1]
    assert [(e.kind, e.key, e.text) for e in events] == [
        ("retrying", "ABC-1", "retrying ABC-1 on pi-lithosai after an API error (an invalid API key)"),
        ("retrying", "ABC-5", "retrying ABC-5 on claude after an API error (an invalid API key)")]
    clock.now = T0 + 179
    assert runner.plan([], [], dry=False)[1] == [] and host.wakes == []
    clock.now = T0 + 180
    runner.plan([], [], dry=False)
    assert host.wakes == [("ABC-1", orch.RETRY_TEXT), ("ABC-5", orch.RETRY_TEXT)], "one retry each"
    clock.now = T0 + 200
    del host.pauses["claude"]  # the retry cleared claude's stop
    assert runner.plan([], [], dry=False)[1] == []
    assert sorted(runner.stops.pause) == ["claude", "pi-lithosai"], "its window is not over: it still holds"
    clock.now = T0 + 361  # both windows end: claude resumed, pi-lithosai stopped again after its retry
    events = runner.plan([], [], dry=False)[1]
    assert sorted((e.kind, e.key) for e in events) == [("paused", "ABC-1"), ("resumed", "ABC-5")]
    assert [e.text for e in events if e.kind == "resumed"] == ["resumed (claude): the newest session no longer shows an invalid API key"]
    assert sorted(runner.stops.pause) == ["pi-lithosai"] and runner.stops.floor("claude") == clock.now
    assert runner.stops.floor("pi-lithosai") is None
    assert len(host.wakes) == 2, "no second retry"
    hard, plain = issue("ABC-3", area="infra", labels=("intricate",)), issue("ABC-2", area="docs")
    assert runner.start_round([plain, hard], [], dry=False) == [
        "ABC-2 waits: its cell runs on pi-lithosai, which is paused", "started ABC-3 (claude-opus-5-5, high)"]


def test_a_stop_holds_the_wake_the_start_and_the_escalation_of_its_profile_only(tmp_path: Path) -> None:
    """The fix waits of the two cells in one round: the issue on the paused profile is held, the other one is not."""
    runner, host, writes, clock = mixed_runner(tmp_path)
    host.wakeable = True
    host.spaces = [{"workspace_id": "w7", "label": "ABC-7 ·", "agent_status": "idle"},
                   {"workspace_id": "w8", "label": "ABC-8 ·", "agent_status": "idle"}]
    easy, hard = waiting_issue(key="ABC-7"), waiting_issue(key="ABC-8", labels=("intricate",))
    prs = [pr(7, "abc-7-x", reviews=ASKED), pr(8, "abc-8-x", reviews=ASKED)]
    host.pauses = {"pi-lithosai": LITHOS_STOP}
    clock.now = T0 + 60
    runner.plan([easy, hard], prs, dry=False)
    assert host.wakes == [("ABC-8", orch.wake_text(8, "changes requested"))], "ABC-7's cell is on the paused profile: no wake"
    clock.now = T0 + 300
    events = runner.plan([easy, hard], prs, dry=False)[1]
    assert [(e.kind, e.key) for e in events] == [("started_fix", "ABC-8")]
    clock.now = T0 + 900
    assert [(e.kind, e.key) for e in runner.plan([easy, hard], prs, dry=False)[1]] == [("escalated", "ABC-8")]
    assert all(key != "ABC-7" for _, key, *_ in writes.done), "ABC-7 waits for its profile: nothing started, nothing escalated"
    host.pauses = {}  # the pause ends: ABC-7's wait counts from then: its session is woken, nothing else yet
    clock.now = T0 + 1000
    events = runner.plan([easy, hard], prs, dry=False)[1]
    assert [e.kind for e in events if e.key == "ABC-7"] == ["woke"] and [e.key for e in events if e.kind == "resumed"] == ["ABC-1"]
    assert runner.stops.floor("pi-lithosai") == T0 + 1000


def test_a_failed_check_of_one_profile_halts_only_its_cells(tmp_path: Path) -> None:
    runner, host, _, _ = mixed_runner(tmp_path)
    claude_key = "cell profile: claude (intricate, judgment, both), its key is not in the keychain: store it"
    runner.prestart = lambda: [orch.Check(orch.OK, "inference profile: pi-lithosai, its key is in the keychain", frozenset({"pi-lithosai"})),
                               orch.Check(orch.FAIL, claude_key, frozenset({"claude"}))]
    plain, hard = issue("ABC-2", area="docs"), issue("ABC-3", area="infra", labels=("intricate",))
    assert runner.start_round([plain, hard], [], dry=False) == [
        "no start of a cell on claude this round: the setup preflight failed",
        "ABC-3 waits: its cell runs on claude, which is halted", "started ABC-2 (pi-lithosai/deepseek-ai/DeepSeek-V4.1-Flash, high)"]
    assert [label for label, _, _ in host.starts] == ["ABC-2 ·"]
    assert runner.halt.scope == {"claude"} and runner.halt.text() == f"cells on claude only: {claude_key}"
    # the halt holds the next round's start of its cell, says nothing new about the same failed check, and starts the other
    later = [issue("ABC-4", area="backend"), issue("ABC-5", area="frontend", labels=("intricate",))]
    assert runner.start_round(later, [], dry=False) == ["ABC-5 waits: its cell runs on claude, which is halted",
                                                        "started ABC-4 (pi-lithosai/deepseek-ai/DeepSeek-V4.1-Flash, high)"]
    # a failed check that no profile owns halts every start, as before
    runner2, host2, _, _ = mixed_runner(tmp_path)
    runner2.prestart = lambda: [(orch.OK, "Linear: answers"), (orch.FAIL, "Paseo daemon profile: cannot verify the running daemon")]
    assert runner2.start_round([plain, hard], [], dry=False) == ["no start this round: the setup preflight failed"]
    assert host2.starts == [] and runner2.halt.scope is None
    assert runner2.halt.text() == "Paseo daemon profile: cannot verify the running daemon"


def test_a_fix_start_of_a_halted_profile_waits_and_the_other_profiles_fix_starts(tmp_path: Path) -> None:
    runner, host, _, clock = mixed_runner(tmp_path)
    runner.prestart = lambda: [orch.Check(orch.FAIL, "claude key missing", frozenset({"claude"}))]
    hard, easy = waiting_issue(key="ABC-6", labels=("intricate",)), waiting_issue(key="ABC-7")
    prs = [pr(6, "abc-6-x", reviews=ASKED), pr(7, "abc-7-x", reviews=ASKED)]
    clock.now = T0 + 300
    events = runner.plan([easy, hard], prs, dry=False)[1]
    assert [(e.kind, e.key) for e in events] == [("halted", "ABC-6"), ("started_fix", "ABC-7")]
    assert [label for label, _, _ in host.starts] == ["ABC-7 ·"] and runner.halt.failed_fix == "ABC-6"
    assert runner.halt.scope == {"claude"}


class FakeDaemon:
    """A Paseo daemon for `daemon_step`: its record (`()`: none runs, None: no record of its own), its agents in a
    turn, and each stop and start; a start that fails raises `start_error`, one that works records the expected profiles."""

    def __init__(self, expected: tuple[str, ...], actual: tuple[str, ...] | None, agents: int = 0) -> None:
        self.profile, self.expected, self.actual, self.agents = expected[0], expected, actual, agents
        self.calls: list[str] = []
        self.start_error: str | None = None
        self.unreadable = False
        self.review_ok = True  # the running daemon started on the review settings of the file

    def review_matches(self) -> bool:
        return self.review_ok

    def profiles(self) -> tuple[str, ...] | None:
        if self.unreadable:
            raise RuntimeError("cannot read the daemon's status")
        return self.actual

    def working(self) -> int:
        return self.agents

    def stop(self) -> None:
        self.calls.append("stop")
        self.actual = ()

    def start(self) -> None:
        self.calls.append("start")
        if self.start_error:
            raise RuntimeError(self.start_error)
        self.actual = self.expected
        self.review_ok = True


def test_the_service_start_switches_the_daemon_when_its_agents_finish(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`main` with a daemon that lacks the key of claude starts nothing while two agents work, and says so in the
    log and the state file; with no agent working it stops the daemon, starts it on the profiles of the config, and starts."""
    config = mixed_config(tmp_path)
    text = config.read_text().replace("dry_run = true", "dry_run = false")
    config.write_text(re.sub(r'(?m)^state_file = "[^"]*"', f'state_file = "{tmp_path}/state.json"', text, count=1))
    reads = orch.Reads(lambda: [issue("ABC-2", area="docs"), issue("ABC-3", area="infra", labels=("intricate",))], list)

    def sleep(seconds: float) -> None:
        raise KeyboardInterrupt
    live = dataclasses.replace(probes(), paseo_profile=lambda: ("pi-lithosai",), cell_key=lambda _: True)
    host, writes, daemon = FakeHost(), FakeWrites(), FakeDaemon(("pi-lithosai", "claude"), ("pi-lithosai",), agents=2)
    assert orch.main(["--config", str(config)], reads, sleep, host=host, writes=writes.writes(), probes=live, daemon=daemon) == 0
    assert host.starts == [] and daemon.calls == [], "no new session and no stop while two agents work"
    assert "halted: switching to pi-lithosai: waiting for 2 agents" in capsys.readouterr().out
    assert json.loads((tmp_path / "state.json").read_text())["halted"] == "switching to pi-lithosai: waiting for 2 agents"
    host, writes, daemon = FakeHost(), FakeWrites(), FakeDaemon(("pi-lithosai", "claude"), ("pi-lithosai",))
    assert orch.main(["--config", str(config)], reads, sleep, host=host, writes=writes.writes(), probes=live, daemon=daemon) == 0
    assert daemon.calls == ["stop", "start"] and sorted(label for label, _, _ in host.starts) == ["ABC-2 ·", "ABC-3 ·"]
    out = capsys.readouterr().out
    assert "Paseo daemon started on pi-lithosai and claude" in out and "halted:" not in out


def daemon_halt(tmp_path: Path) -> tuple[orch.Config, orch.Halt]:
    return orch.load_config(config_with(tmp_path, session_host="session_host = 'paseo'")), orch.Halt()


def test_no_daemon_starts_one_with_the_profile_in_force(tmp_path: Path) -> None:
    """no daemon runs (the first round after a restart of the Mac): the service starts it, and halts nothing."""
    cfg, halt = daemon_halt(tmp_path)
    daemon, said = FakeDaemon(("claude",), ()), []
    assert orch.daemon_step(cfg, halt, daemon, 10, said.append) is None
    assert daemon.calls == ["start"] and halt.cause is None and said == ["Paseo daemon started on claude"]
    assert orch.daemon_step(cfg, halt, daemon, 20, said.append) is None and daemon.calls == ["start"], "a match does nothing"


def test_a_daemon_on_another_profile_halts_the_starts_until_its_agents_finish_then_restarts(tmp_path: Path) -> None:
    cfg, halt = daemon_halt(tmp_path)
    daemon, said = FakeDaemon(("claude",), ("pi-lithosai",), agents=2), []
    assert orch.daemon_step(cfg, halt, daemon, 10, said.append) is None
    assert daemon.calls == [] and halt.text() == "switching to claude: waiting for 2 agents" and halt.blocks("claude")
    assert halt.unreported is None, "the owner asked for the switch: no Linear notice"
    halt.check = lambda: []
    assert halt.recover(cfg, 10_000) is None and halt.cause, "a passing preflight does not lift the switch"
    daemon.agents = 1
    orch.daemon_step(cfg, halt, daemon, 20, said.append)
    assert halt.text() == "switching to claude: waiting for 1 agent" and daemon.calls == []
    daemon.agents = 0
    resumed = orch.daemon_step(cfg, halt, daemon, 30, said.append)
    assert daemon.calls == ["stop", "start"] and halt.cause is None and not halt.daemon_hold
    assert resumed is not None and resumed.endswith("the Paseo daemon runs on claude")
    assert said == ["Paseo daemon stopped: it held pi-lithosai, and no agent works", "Paseo daemon started on claude"]
    # a daemon with no record of its own (not started by `paseo-daemon`) is switched the same way
    unknown = FakeDaemon(("claude",), None)
    orch.daemon_step(cfg, halt, unknown, 40, said.append)
    assert unknown.calls == ["stop", "start"] and said[-2] == "Paseo daemon stopped: it held no record, and no agent works"


def test_a_change_of_the_review_settings_alone_switches_the_daemon(tmp_path: Path) -> None:
    """the daemon holds the right profiles but started on other review settings: with agents working the starts
    wait, with none the service stops the daemon and starts it again, with no `paseo daemon stop` by hand."""
    cfg, halt = daemon_halt(tmp_path)
    daemon, said = FakeDaemon(("claude",), ("claude",), agents=1), []
    daemon.review_ok = False
    assert orch.daemon_step(cfg, halt, daemon, 10, said.append) is None
    assert daemon.calls == [] and halt.text() == "switching to claude: waiting for 1 agent"
    daemon.agents = 0
    resumed = orch.daemon_step(cfg, halt, daemon, 20, said.append)
    assert daemon.calls == ["stop", "start"] and resumed is not None and halt.cause is None
    assert said == ["Paseo daemon stopped: it held claude with other review settings, and no agent works",
                    "Paseo daemon started on claude"]
    assert orch.daemon_step(cfg, halt, daemon, 30, said.append) is None and daemon.calls == ["stop", "start"], "a match does nothing"


def test_the_review_digest_is_the_one_that_agent_user_records(tmp_path: Path) -> None:
    """both sides digest the entries of the profile set, so a changed model, effort or cell is a difference, and an
    entry outside the set is none."""
    spec = importlib.util.spec_from_file_location("agent_user_for_digest", Path(__file__).resolve().parents[1] / ".workflow" / "agent_user.py")
    assert spec and spec.loader
    agent = importlib.util.module_from_spec(spec)
    sys.modules["agent_user_for_digest"] = agent
    spec.loader.exec_module(agent)
    file = tmp_path / "local-review.json"
    entries = {"claude": {"model": "m1", "effort": "high", "cells": ["claude"]}, "pi-ollama": {"model": "m2", "effort": "low"}}
    file.write_text(json.dumps(entries))
    agent.review_json = lambda: (file, json.loads(file.read_text()))
    before = orch.review_digest(file, ["claude"])
    assert before is not None and before == agent.review_digest(["claude"])
    entries["pi-ollama"]["model"] = "m3"
    file.write_text(json.dumps(entries))
    assert orch.review_digest(file, ["claude"]) == before, "a profile outside the set changes nothing"
    entries["claude"]["effort"] = "low"
    file.write_text(json.dumps(entries))
    assert orch.review_digest(file, ["claude"]) != before and orch.review_digest(file, ["claude"]) == agent.review_digest(["claude"])
    assert orch.review_digest(file, ["gone"]) is None and orch.review_digest(tmp_path / "none.json", ["claude"]) is None


def test_the_real_daemon_compares_the_recorded_digest_with_the_file(tmp_path: Path) -> None:
    """`PaseoDaemon.review_matches` reads the record's `review` against the file's digest: a record without it (a
    start from before this change) differs, and no file set is no check."""
    marker = tmp_path / ".local/state/orchestrator/paseo-profile.json"
    marker.parent.mkdir(parents=True)
    file = tmp_path / "local-review.json"
    file.write_text(json.dumps({"claude": {"model": "m", "effort": "high", "cells": ["claude"]}}))
    digest = orch.review_digest(file, ["claude"])

    def status(argv: list[str]) -> Any:
        return orch.subprocess.CompletedProcess(argv, 0, json.dumps({"localDaemon": "running", "pid": 123, "startedAt": "new"}))

    class Host:
        run = staticmethod(status)
    daemon = orch.PaseoDaemon("claude", ["claude"], Host(), home=tmp_path, review_file=file)  # type: ignore[arg-type]
    marker.write_text(json.dumps({"profile": "claude", "pid": 123, "startedAt": "new"}))
    assert not daemon.review_matches(), "no digest in the record"
    marker.write_text(json.dumps({"profile": "claude", "pid": 123, "startedAt": "new", "review": digest}))
    assert daemon.review_matches()
    file.write_text(json.dumps({"claude": {"model": "m", "effort": "low", "cells": ["claude"]}}))
    assert not daemon.review_matches()
    file.unlink()
    assert daemon.review_matches(), "a file that cannot be read stops no daemon"
    assert orch.PaseoDaemon("claude", ["claude"], Host(), home=tmp_path).review_matches()  # type: ignore[arg-type]


def test_a_failed_daemon_start_keeps_the_halt_with_its_error_and_tries_again(tmp_path: Path) -> None:
    cfg, halt = daemon_halt(tmp_path)
    daemon, said = FakeDaemon(("claude",), ()), []
    daemon.start_error = "Tailscale is not connected"
    orch.daemon_step(cfg, halt, daemon, 10, said.append)
    assert halt.text() == "Paseo daemon not started on claude: Tailscale is not connected" and halt.daemon_hold
    assert halt.unreported == (halt.text(), 10), "a failed start notifies the owner"
    halt.check = lambda: []
    assert halt.recover(cfg, 10_000) is None and halt.cause, "only the daemon's start lifts it"
    orch.daemon_step(cfg, halt, daemon, 20, said.append)
    assert daemon.calls == ["start", "start"] and halt.at == 10, "the same cause again keeps its time"
    daemon.start_error = None
    assert orch.daemon_step(cfg, halt, daemon, 30, said.append) is not None and halt.cause is None
    unread = FakeDaemon(("claude",), ("pi-lithosai",))
    unread.unreadable = True
    orch.daemon_step(cfg, halt, unread, 40, said.append)
    assert unread.calls == [] and halt.text() == "Paseo daemon not started on claude: cannot read the daemon's status"


def test_a_started_daemon_on_other_profiles_keeps_the_halt_and_notifies_once(tmp_path: Path) -> None:
    """Review R1-3: `paseo-daemon` takes its profiles from `local-review.json`, the service from its config. A started daemon
    that differs keeps the halt with a known cause and one notice, and starts no session on it."""
    cfg, halt = daemon_halt(tmp_path)

    class Stale(FakeDaemon):
        def start(self) -> None:
            self.calls.append("start")
            self.actual = ("pi-lithosai",)
    daemon, said = Stale(("claude",), ()), []
    assert orch.daemon_step(cfg, halt, daemon, 10, said.append) is None
    cause = ("Paseo daemon not started on claude: the started daemon holds pi-lithosai, expected claude: run "
             "`owlcode restart`")
    assert halt.text() == cause and halt.daemon_hold and halt.unreported == (cause, 10) and said == []
    orch.daemon_step(cfg, halt, daemon, 20, said.append)  # the next round: stop, start, the same cause, its time kept
    assert daemon.calls == ["start", "stop", "start"] and halt.at == 10


def test_a_started_daemon_on_other_review_settings_keeps_the_halt(tmp_path: Path) -> None:
    """a start that leaves the record on other review settings (an old `agent_user.py`) keeps the halt with its cause."""
    cfg, halt = daemon_halt(tmp_path)

    class Stale(FakeDaemon):
        def start(self) -> None:
            super().start()
            self.review_ok = False
    daemon = Stale(("claude",), ())
    assert orch.daemon_step(cfg, halt, daemon, 10, print) is None
    assert halt.text() == ("Paseo daemon not started on claude: the started daemon holds other review settings than the installed "
                           "local-review.json: run `owlcode restart`") and halt.daemon_hold


def test_the_owners_pause_stays_the_banner_while_the_daemon_switches(tmp_path: Path) -> None:
    cfg, halt = daemon_halt(tmp_path)
    halt.hold = lambda: True
    halt.pause_step(cfg, 0)
    daemon = FakeDaemon(("claude",), ("pi-lithosai",), agents=1)
    orch.daemon_step(cfg, halt, daemon, 10, print)
    assert halt.cause == orch.PAUSED and daemon.calls == []
    daemon.agents = 0
    assert orch.daemon_step(cfg, halt, daemon, 20, print) is None
    assert daemon.calls == ["stop", "start"] and halt.cause == orch.PAUSED, "the switch goes on; the pause stays"


def test_the_real_daemon_starts_through_agent_user_in_its_own_session_and_says_only_known_causes(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """the start is the installed `agent_user.py paseo-daemon --profile NAME`; its raw output never reaches the halt
    (a Linear notice), only a known cause or the exit code. The agents in a turn count background ones too."""
    ran: list[list[str]] = []
    agents = [{"status": "running"}, {"status": "idle"}, {"status": "initializing"}, {"status": "error"}]

    def run(argv: Sequence[str]) -> Any:
        ran.append(list(argv))
        if argv[1:3] == ["ls", "-g"]:
            return orch.subprocess.CompletedProcess(argv, 0, json.dumps(agents))
        return orch.subprocess.CompletedProcess(argv, 1 if argv[-1] == "stop" else 0, "")
    spawned: list[list[str]] = []
    envs: list[dict[str, str]] = []
    reply = orch.subprocess.CompletedProcess([], 0, "")

    def spawn(argv: Sequence[str], env: dict[str, str]) -> Any:
        spawned.append(list(argv))
        envs.append(env)
        return reply
    monkeypatch.setenv("DEF_LINEAR_API_KEY", "lin_api_owl")
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_abc")
    monkeypatch.setenv("KEEP_ME", "1")
    daemon = orch.PaseoDaemon("claude", ("claude",), orch.PaseoHost(run=run), tmp_path, spawn,
                              hide={"LINEAR_API_KEY", "DEF_LINEAR_API_KEY"})
    assert daemon.working() == 2 and ran[-1] == ["paseo", "ls", "-g", "--json", "--background"]
    daemon.start()
    assert spawned == [["/usr/bin/python3", "-I", str(orch.agent_wrapper()), "paseo-daemon", "--profile", "claude"]]
    assert "DEF_LINEAR_API_KEY" not in envs[0] and "LINEAR_API_KEY" not in envs[0] and envs[0]["KEEP_ME"] == "1", \
        "no team's Linear key reaches the daemon, so no agent inherits a second workspace's key"
    reply = orch.subprocess.CompletedProcess([], 1, "", "agent_user.py: the claude-token is not in the Keychain: run `user` sk-x")
    with pytest.raises(RuntimeError, match=r"^a key is not in the Keychain$"):
        daemon.start()
    for err in ("--profile pi-ollama is not the orchestrator's inference_profile, claude: run it",
                "cannot read /x/inference-profile.json (OSError)",
                "/x/inference-profile.json names no profile that this script knows"):
        reply = orch.subprocess.CompletedProcess([], 1, "", f"agent_user.py: {err}")
        with pytest.raises(RuntimeError, match=r"the installed inference-profile\.json differs"):
            daemon.start()
    reply = orch.subprocess.CompletedProcess([], 3, "secret output", "")
    with pytest.raises(RuntimeError, match=r"^exit 3$"):
        daemon.start()
    with pytest.raises(RuntimeError, match="paseo daemon stop exited 1"):
        daemon.stop()
    status = {"code": 0, "out": json.dumps({"localDaemon": "stopped"})}
    reader = orch.PaseoDaemon("claude", ("claude",), orch.PaseoHost(
        run=lambda argv: orch.subprocess.CompletedProcess(argv, status["code"], status["out"])), tmp_path, spawn)
    assert reader.profiles() == (), "no daemon runs"
    status.update(code=1, out="")
    with pytest.raises(RuntimeError, match="cannot read the daemon's status"):
        reader.profiles()  # the service never stops a daemon that it cannot read


def test_a_failed_agent_list_halts_with_a_known_cause_and_no_raw_output(tmp_path: Path) -> None:
    cfg, halt = daemon_halt(tmp_path)

    def run(argv: Sequence[str]) -> Any:
        if argv[1:3] == ["ls", "-g"]:
            return orch.subprocess.CompletedProcess(argv, 1, "", "raw paseo output sk-x")
        return orch.subprocess.CompletedProcess(argv, 0, json.dumps({"localDaemon": "running", "pid": 1, "startedAt": "t"}))
    daemon = orch.PaseoDaemon("claude", ("claude",), orch.PaseoHost(run=run), tmp_path)
    orch.daemon_step(cfg, halt, daemon, 10, print)
    assert halt.text() == "Paseo daemon not started on claude: cannot list the agents"


def test_a_confirmed_launch_does_not_lift_the_daemons_switch(tmp_path: Path) -> None:
    """a launch that the host confirms during the switch ends no halt: only `daemon_step` lifts it."""
    runner, host, writes, _ = runner_of(tmp_path)
    runner.writes = writes.writes()
    runner.confirm_launch = lambda: [orch.Event("started", "ABC-9", "started ABC-9")]
    daemon = FakeDaemon((runner.cfg.inference_profile,), ("pi-lithosai",), agents=1)
    data, path = orch.Reads(lambda: [issue("ABC-635", area="docs")], list), tmp_path / "state.json"
    orch.round_all([orch.Team(runner.cfg, data, runner)], [].append, dry=False, state_file=path, daemon=daemon)
    assert json.loads(path.read_text())["halted"].startswith("switching to ") and not host.starts


def test_main_keeps_the_real_daemon_for_its_own_paseo_host_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """the service (no test host, not dry, Paseo) builds the daemon on `inference_profile` and `daemon_profiles`."""
    config = mixed_config(tmp_path)
    built: list[tuple[str, tuple[str, ...]]] = []

    hidden: list[frozenset[str]] = []
    files: list[Any] = []

    class Recorded(FakeDaemon):
        def __init__(self, profile: str, expected: tuple[str, ...], host: Any, hide: Any = (), review_file: Any = None) -> None:
            built.append((profile, tuple(expected)))
            hidden.append(frozenset(hide))
            files.append(review_file)
            super().__init__(tuple(expected), tuple(expected))
    monkeypatch.setattr(orch, "PaseoDaemon", Recorded)
    monkeypatch.setattr(orch, "make_host", lambda cfg: orch.PaseoHost(run=lambda argv: orch.subprocess.CompletedProcess(argv, 0, "[]")))
    reads = orch.Reads(list, list)
    live = dataclasses.replace(probes(), paseo_profile=lambda: ("pi-lithosai", "claude"), cell_key=lambda _: True)

    def sleep(seconds: float) -> None:
        raise KeyboardInterrupt
    assert orch.main(["--config", str(config), "--once"], reads, sleep, writes=FakeWrites().writes(), probes=live) == 0
    assert built == [], "a dry run builds none"
    config.write_text(config.read_text().replace("dry_run = true", "dry_run = false") + (
        '\n[[product]]\nteam = "DEF"\nrepo = "owner/def"\nrun_cwd = "~/def"\nlinear_key_env = "DEF_LINEAR_API_KEY"\n'))
    orch.main(["--config", str(config)], {"ABC": reads, "DEF": reads}, sleep, writes=FakeWrites().writes(), probes=live)
    assert built == [("pi-lithosai", ("pi-lithosai", "claude"))]
    assert files == [Path(orch.__file__).resolve().parent / "local-review.json"], "the service reads the installed file"
    assert hidden == [{"LINEAR_API_KEY", "DEF_LINEAR_API_KEY"}], \
        "the variable of each team's Linear key, so a second workspace's key never reaches the daemon"


def test_spawn_daemon_runs_the_start_in_a_session_of_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    """launchd ends the process group of a job that ends: the daemon's start leaves the service's group."""
    seen: dict[str, Any] = {}
    monkeypatch.setattr(orch.subprocess, "run", lambda argv, **kw: seen.update(kw) or orch.subprocess.CompletedProcess(argv, 0))
    orch.spawn_daemon(["x"], {"A": "1"})
    assert seen["start_new_session"] is True and seen["stdin"] == orch.subprocess.DEVNULL and seen["env"] == {"A": "1"}


def test_a_failed_check_that_no_profile_owns_halts_every_cell_of_the_mix(tmp_path: Path) -> None:
    """A Linear or GitHub answer, the host's list, an unreadable daemon: no profile owns the failure, so no cell starts."""
    runner, host, _, _ = mixed_runner(tmp_path)
    runner.prestart = lambda: [(orch.FAIL, "Paseo session list: no answer (RuntimeError)")]
    issues = [issue("ABC-2", area="docs"), issue("ABC-3", area="infra", labels=("intricate",))]
    assert runner.start_round(issues, [], dry=False) == ["no start this round: the setup preflight failed"]
    assert host.starts == [] and runner.halt.scope is None and runner.halted("claude") and runner.halted("pi-lithosai")
    assert runner.start_round(issues, [], dry=False) == [], "the halt holds every cell, with no line for each issue"


def test_a_stop_of_a_profile_that_is_not_in_force_holds_the_whole_loop_when_no_cell_runs_elsewhere(tmp_path: Path) -> None:
    """The owner switched `inference_profile`: an old agent labelled `claude` stopped. With one profile in force the loop acts
    as before profiles: the start, the wake, the fix start and the escalation all wait (a review finding on the first version)."""
    runner, host, writes, clock = runner_of(tmp_path, inference_profile="pi-ollama")
    host.wakeable = True
    idle_host(host)
    host.pauses = {"claude": ("ABC-9", "the usage limit")}
    issues, prs = [waiting_issue(), issue("ABC-3", area="docs")], [pr(7, "abc-7-x", reviews=ASKED)]
    for at in (T0 + 60, T0 + 300, T0 + 900):
        clock.now = at
        runner.plan(issues, prs, dry=False)
    assert host.starts == [] and host.wakes == [] and writes.done == []
    assert runner.paused("pi-ollama") and runner.blocked("pi-ollama") == "paused"


def test_a_halt_of_two_profiles_lets_one_go_when_its_check_passes(tmp_path: Path) -> None:
    cfg = fix_cfg(tmp_path)
    claude, lithos = frozenset({"claude"}), frozenset({"pi-lithosai"})
    fails: list[orch.Line] = [orch.Check(orch.FAIL, "claude key missing", claude), orch.Check(orch.FAIL, "lithosai models gone", lithos)]
    halt = orch.Halt(check=lambda: fails)
    halt.enter_failed(cfg, 0, orch.failed_checks(fails))
    assert halt.scope == claude | lithos and halt.cause == "claude key missing; lithosai models gone"
    fails[:] = [orch.Check(orch.FAIL, "lithosai models gone", lithos)]  # the key of claude is back
    assert halt.recover(cfg, halt.next_check) is None
    assert halt.scope == lithos and halt.cause == "lithosai models gone" and not halt.blocks("claude") and halt.blocks("pi-lithosai")
    assert halt.text() == "cells on pi-lithosai only: lithosai models gone"
    assert halt.unreported == ("cells on pi-lithosai only: lithosai models gone", halt.next_check - 60 * cfg.halt_check_minutes)
    fails[:] = []
    assert halt.recover(cfg, halt.next_check) is not None and halt.cause is None and halt.scope is None


def test_a_halt_of_every_start_narrows_to_the_profile_whose_check_still_fails(tmp_path: Path) -> None:
    """The daemon cannot be read (no profile owns it) and pi-lithosai's key fails too. Once the daemon reads
    again, only the cells on pi-lithosai stay halted, the banner says so, and a cell on claude starts."""
    runner, host, _, _ = mixed_runner(tmp_path)
    fails: list[orch.Line] = [orch.Check(orch.FAIL, "Paseo daemon profiles: cannot verify the running daemon"),
                              orch.Check(orch.FAIL, "inference profile: pi-lithosai, its key is not in the keychain",
                                         frozenset({"pi-lithosai"}))]
    runner.prestart = lambda: fails
    runner.halt.check = lambda: fails
    hard, plain = issue("ABC-3", area="infra", labels=("intricate",)), issue("ABC-2", area="docs")
    assert runner.start_round([plain, hard], [], dry=False) == ["no start this round: the setup preflight failed"]
    assert runner.halt.scope is None and host.starts == []
    fails[:] = [fails[1]]  # the daemon reads again
    assert runner.halt.recover(runner.cfg, runner.halt.next_check) is None
    assert runner.halt.scope == {"pi-lithosai"}
    assert runner.halt.text() == "cells on pi-lithosai only: inference profile: pi-lithosai, its key is not in the keychain"
    assert runner.start_round([plain, hard], [], dry=False) == ["ABC-2 waits: its cell runs on pi-lithosai, which is halted",
                                                                "started ABC-3 (claude-opus-5-5, high)"]
    fails[:] = []
    assert runner.halt.recover(runner.cfg, runner.halt.next_check) is not None and runner.halt.cause is None


def test_a_scoped_halt_notice_names_the_halted_profiles(tmp_path: Path) -> None:
    """The Linear notice says which profiles halt, as the banner does."""
    cfg = fix_cfg(tmp_path)
    halt = orch.Halt()
    halt.enter(cfg, 5, "claude key missing", frozenset({"claude"}))
    assert halt.unreported == ("cells on claude only: claude key missing", 5)
    whole = orch.Halt()
    whole.enter(cfg, 5, "Paseo daemon: cannot verify")
    assert whole.unreported == ("Paseo daemon: cannot verify", 5)
    wrote: list[tuple[str | None, float]] = []
    halt.notify(lambda cause, at: wrote.append((cause, at)), 6)
    assert wrote == [("cells on claude only: claude key missing", 5)]
    assert "cells on claude only: claude key missing" in halt.reported


def test_a_halt_that_narrows_gives_the_released_profiles_a_fix_wait_floor(tmp_path: Path) -> None:
    """A profile that leaves the halt counts its fix waits from the narrowing, as after a resume."""
    runner, _, _, _ = mixed_runner(tmp_path)
    fails: list[orch.Line] = [orch.Check(orch.FAIL, "Paseo daemon profiles: cannot verify the running daemon"),
                              orch.Check(orch.FAIL, "pi-lithosai key missing", frozenset({"pi-lithosai"}))]
    runner.prestart = lambda: fails
    runner.halt.check = lambda: fails
    runner.halt.enter_failed(runner.cfg, 0, orch.failed_checks(fails))
    fails[:] = [fails[1]]  # the daemon reads again: the halt narrows to pi-lithosai
    at = runner.halt.next_check
    runner.clock = lambda: at
    team = orch.Team(runner.cfg, orch.Reads(list, list), runner)
    assert runner.stops.floor("claude") is None
    orch.round_all([team], lambda _: None, dry=False)
    assert runner.halt.scope == {"pi-lithosai"} and runner.stops.floor("claude") == at
    assert runner.stops.floor("pi-lithosai") is None, "a profile that stays halted gets no floor"


def test_a_halt_that_narrows_floors_only_the_profiles_it_held(tmp_path: Path) -> None:
    """A profile outside a scoped halt gets no floor when the halt narrows."""
    runner, _, _, _ = mixed_runner(tmp_path)
    held, kept = "claude", "pi-lithosai"
    runner.cfg = dataclasses.replace(runner.cfg, cell_profiles=(*runner.cfg.cell_profiles, "pi-ollama"))  # a third profile
    others = {runner.cfg.inference_profile, *runner.cfg.cell_profiles} - {held, kept}
    assert others == {"pi-ollama"}
    fails: list[orch.Line] = [orch.Check(orch.FAIL, "claude key missing", frozenset({held})),
                              orch.Check(orch.FAIL, "lithosai key missing", frozenset({kept}))]
    runner.prestart = lambda: fails
    runner.halt.check = lambda: fails
    runner.halt.enter_failed(runner.cfg, 0, orch.failed_checks(fails))
    fails[:] = [fails[1]]  # claude's check passes: the halt narrows to pi-lithosai
    at = runner.halt.next_check
    runner.clock = lambda: at
    orch.round_all([orch.Team(runner.cfg, orch.Reads(list, list), runner)], lambda _: None, dry=False)
    assert runner.stops.floor(held) == at and runner.stops.floor(kept) is None
    assert all(runner.stops.floor(name) is None for name in others), "a profile the halt never held gets no floor"


def test_a_state_file_from_before_the_profiles_has_one_pause_row_and_the_text_still_reads_it() -> None:
    base = {"time": "2026-11-02T10:00:00Z", "building": 0, "max_runs": 2, "reviewing": 0, "yours": [], "slots": [], "areas": [],
            "other_waits": [], "stacks": [], "prs": [], "events": [], "hands_off": 0, "close_by_hand": []}
    paused = dict(base, pause={"key": "ABC-9", "cause": "the usage limit", "phase": "paused"})
    retrying = dict(base, pause={"key": "ABC-9", "cause": "an invalid API key", "phase": "retrying"})
    assert "paused: the usage limit" in orch.plan_text(paused, [])
    assert "retrying ABC-9 after an API error (an invalid API key)" in orch.plan_text(retrying, [])
    assert "retrying ABC-9 on pi-lithosai after an API error (x)" in orch.plan_text(
        dict(base, pause=[{"profile": "pi-lithosai", "key": "ABC-9", "cause": "x", "phase": "retrying"}]), [])


def test_a_scoped_halt_shows_its_profiles_in_the_banner_and_the_state_file(tmp_path: Path) -> None:
    runner, _, _, _ = mixed_runner(tmp_path)
    runner.prestart = lambda: [orch.Check(orch.FAIL, "claude key missing", frozenset({"claude"}))]
    state, out = tmp_path / "state.json", []
    reads = orch.Reads(lambda: [issue("ABC-3", area="infra", labels=("intricate",))], list)
    assert orch.round_once(runner.cfg, reads, out.append, runner, dry=False, state_file=state)
    assert "halted: cells on claude only: claude key missing" in out
    assert json.loads(state.read_text())["halted"] == "cells on claude only: claude key missing"
    assert orch.halt_banner({"halted": runner.halt.text()}) == ["halted: cells on claude only: claude key missing"]


def test_a_halt_of_some_profiles_takes_more_in_keeps_its_times_and_ends_by_its_own_checks(tmp_path: Path) -> None:
    cfg = fix_cfg(tmp_path)
    claude, lithos = frozenset({"claude"}), frozenset({"pi-lithosai"})
    fails: list[orch.Line] = []
    halt = orch.Halt(check=lambda: fails)
    halt.enter(cfg, 100, "claude key missing", claude)
    check = halt.next_check
    halt.enter(cfg, 200, "claude key missing", claude)  # the same failed check found again by another profile's start
    assert halt.next_check == check and halt.at == 100, "no move of the recovery check"
    halt.enter(cfg, 250, "lithosai models gone", lithos)
    assert halt.scope == claude | lithos and halt.cause == "claude key missing; lithosai models gone"
    assert halt.blocks("claude") and halt.blocks("pi-lithosai") and not halt.blocks("pi-ollama")
    halt.enter(cfg, 260, "lithosai models gone", lithos)
    assert halt.cause == "claude key missing; lithosai models gone", "the same cause is not joined twice"
    # recovery: a failed check of a profile that the halt does not hold never keeps it; one that it holds does
    fails[:] = [orch.Check(orch.FAIL, "pi-ollama broken", frozenset({"pi-ollama"}))]
    assert halt.recover(cfg, halt.next_check) is not None and halt.cause is None and halt.scope is None
    halt.enter(cfg, 1000, "claude key missing", claude)
    fails[:] = [orch.Check(orch.FAIL, "claude key missing", claude)]
    assert halt.recover(cfg, halt.next_check) is None and halt.cause == "claude key missing"
    fails[:] = [orch.Check(orch.FAIL, "Linear: no answer")]  # no profile owns it
    assert halt.recover(cfg, halt.next_check) is None
    # a halt of every start stays whole: a profile's failed check adds nothing, and a global one replaces a scoped halt
    halt.enter(cfg, 2000, "Paseo daemon: cannot verify")
    assert halt.scope is None
    halt.enter(cfg, 2100, "claude key missing", claude)
    assert halt.scope is None and halt.cause == "Paseo daemon: cannot verify"
    other = orch.Halt()
    other.enter(cfg, 1, "claude key missing", claude)
    other.enter(cfg, 2, "Paseo daemon: cannot verify")
    assert other.scope is None and other.cause == "Paseo daemon: cannot verify"


def test_the_preflight_gives_each_check_to_the_profiles_it_belongs_to(tmp_path: Path) -> None:
    cfg = orch.load_config(mixed_config(tmp_path))

    def run(daemon: Any, key: bool = True, model: bool = True) -> list[orch.Check]:
        return orch.preflight(cfg, lambda: True, lambda _: model, lambda: daemon, lambda _: key)
    ok = run(("pi-lithosai", "claude"))
    assert [(c.level, sorted(c.profiles or ())) for c in ok[:3]] == [(orch.OK, ["pi-lithosai"]), (orch.OK, ["claude"]), (orch.OK, [])]
    assert not [c for c in run(("pi-lithosai",)) if c.level == orch.FAIL], \
        "A daemon that lacks the key of claude is the service's switch (`daemon_step`), no failed check"
    assert not [c for c in run(("claude", "pi-lithosai")) if c.level == orch.FAIL], "nor one with another profile's review"
    assert [c.profiles for c in run(None) if c.level == orch.FAIL] == [None], "a daemon that cannot be read: every start"
    assert [c.profiles for c in run(("pi-lithosai", "claude"), key=False) if c.level == orch.FAIL] == [frozenset({"claude"})]
    models = [c for c in run(("pi-lithosai", "claude"), model=False) if c.level == orch.FAIL]
    assert models and all(c.profiles == {"pi-lithosai"} for c in models), "a model that pi lacks fails its profile's cells"
    assert orch.failed_checks([(orch.OK, "a"), (orch.FAIL, "b"), orch.Check(orch.FAIL, "c", frozenset({"x"}))]) == [
        (orch.FAIL, "b"), (orch.FAIL, "c")]


def labelled_agents(*agents: tuple[str, str, str | None, str, str, str]) -> str:
    """`paseo ls -g --json` for (id, name, profile label or None, provider, status, created) each (CLI 0.17.1 prints `labels`)."""
    return json.dumps([{"id": i, "shortId": i[:6], "name": n, "provider": prov, "status": s, "created": c,
                        "labels": {"orchestrator": n.split()[0], **({"profile": p} if p else {})}}
                       for i, n, p, prov, s, c in agents])


def mixed_paseo(tmp_path: Path, *agents: tuple[str, str, str | None, str, str, str], log: str = "", logs: dict[str, str] | None = None,
                **config: str) -> Any:
    """A Paseo host with the stop patterns of the issue's mix. `logs` gives each agent its own log (by id), else all share `log`."""
    cfg = orch.load_config(mixed_config(tmp_path, **config))
    fake = FakePaseo()
    fake.replies["ls"], fake.replies["logs"] = labelled_agents(*agents), log

    def run(argv: Any) -> Any:
        done = fake(argv)
        if argv[1] == "logs" and logs is not None:
            return orch.subprocess.CompletedProcess(argv, 0, logs[argv[2]], "")
        return done
    return orch.PaseoHost(run, cfg.stop_patterns, by_provider=orch.harness_stops(cfg))


LOG_402 = '[System Error] 402: {"error":{"message":"top up","code":"insufficient_quota"}} (stopReason=error, model=pi-lithosai/x)\n'


def test_the_paseo_host_gives_the_newest_stop_of_each_profile(tmp_path: Path) -> None:
    # a newer running claude agent does not clear LithosAI's stop: each profile has its own newest session
    host = mixed_paseo(tmp_path, ("a-0001", "ABC-1 ·", "pi-lithosai", "pi", "error", "5 minutes ago"),
                       ("a-0002", "ABC-2 ·", "claude", "claude", "running", "1 minutes ago"), log=LOG_402)
    assert host.paused() == {"pi-lithosai": ("ABC-1", "no credit left on the provider")}
    # the newest of its own profile decides: a newer running agent of the profile ends the stop
    host = mixed_paseo(tmp_path, ("a-0001", "ABC-1 ·", "pi-lithosai", "pi", "error", "5 minutes ago"),
                       ("a-0003", "ABC-3 ·", "pi-lithosai", "pi", "running", "1 minutes ago"), log=LOG_402)
    assert host.paused() == {}
    # both profiles stopped: one entry for each, and each reads its own harness's words
    host = mixed_paseo(tmp_path, ("a-0001", "ABC-1 ·", "pi-lithosai", "pi", "error", "5 minutes ago"),
                       ("a-0002", "ABC-2 ·", "claude", "claude", "error", "2 minutes ago"),
                       logs={"a-0001": LOG_402, "a-0002": "[System Error] Invalid API key · Fix external API key\n"})
    assert host.paused() == {"pi-lithosai": ("ABC-1", "no credit left on the provider"), "claude": ("ABC-2", "an invalid API key")}


def test_the_paseo_host_reads_the_harness_from_the_provider_before_the_slash(tmp_path: Path) -> None:
    # the real shape of `paseo ls -g --json` (CLI 0.17.1): the harness, a slash and the model
    host = mixed_paseo(tmp_path, ("a-0001", "ABC-1 ·", "pi-lithosai", "pi/pi-lithosai/x", "error", "5 minutes ago"),
                       ("a-0002", "ABC-2 ·", "claude", "claude/claude-sonnet-5-5", "error", "2 minutes ago"),
                       logs={"a-0001": LOG_402, "a-0002": "[System Error] Invalid API key · Fix external API key\n"})
    assert host.paused() == {"pi-lithosai": ("ABC-1", "no credit left on the provider"), "claude": ("ABC-2", "an invalid API key")}


def test_a_pi_agent_is_read_with_pi_words_while_claude_code_is_the_profile_in_force(tmp_path: Path) -> None:
    # the other direction: without the split, `pi/pi-lithosai/x` falls back to Claude Code's words and pauses nothing
    host = mixed_paseo(tmp_path, ("a-0001", "ABC-1 ·", "pi-lithosai", "pi/pi-lithosai/x", "error", "5 minutes ago"), log=LOG_402,
                       profile="claude", claude_intricate='["deepseek-ai/DeepSeek-V4.1-Flash", "high", "pi-lithosai"]')
    assert host.paused() == {"pi-lithosai": ("ABC-1", "no credit left on the provider")}


def test_a_stopped_agent_with_no_profile_label_counts_only_when_it_is_the_newest_of_all(tmp_path: Path) -> None:
    newest = mixed_paseo(tmp_path, ("a-0001", "ABC-1 ·", "pi-lithosai", "pi", "running", "10 minutes ago"),
                         ("a-0004", "ABC-4 ·", None, "pi", "error", "1 minutes ago"), log=LOG_402)
    assert newest.paused() == {None: ("ABC-4", "no credit left on the provider")}
    older = mixed_paseo(tmp_path, ("a-0001", "ABC-1 ·", "pi-lithosai", "pi", "running", "1 minutes ago"),
                        ("a-0004", "ABC-4 ·", None, "pi", "error", "10 minutes ago"), log=LOG_402)
    assert older.paused() == {}, "as before: the newest agent is running, so nothing stopped"
    wrong = mixed_paseo(tmp_path, ("a-0001", "ABC-1 ·", "x; rm -rf ~", "pi", "error", "1 minutes ago"), log=LOG_402)
    assert wrong.paused() == {None: ("ABC-1", "no credit left on the provider")}, "a label of no setting shape is no label"


def test_the_owners_pause_halts_every_start_through_a_passing_preflight_until_the_file_goes(tmp_path: Path) -> None:
    """`install.py pause` writes a file beside the config; the halt then lasts until `resume` removes it, not until a
    preflight passes. It writes no setup notice, and the state file and the banner show it."""
    runner, host, writes, clock = runner_of(tmp_path)
    notices, log = [], []
    flag = tmp_path / "orchestrator.paused"
    assert orch.pause_path(Path("/usr/local/etc/orchestrator.toml")) == Path("/usr/local/etc/orchestrator.paused")
    runner.halt.check = lambda: [(orch.OK, "preflight")]  # the preflight passes at every check
    runner.halt.hold = flag.exists
    runner.writes = dataclasses.replace(writes.writes(), notice=lambda cause, at: notices.append(cause))
    data = orch.Reads(lambda: [issue("ABC-635", area="docs")], list)
    path = tmp_path / "state.json"

    def poll() -> bool:
        return orch.round_all([orch.Team(runner.cfg, data, runner)], log.append, dry=False, state_file=path)

    flag.write_text("")
    assert poll()
    assert json.loads(path.read_text())["halted"] == "paused by the owner" and "halted: paused by the owner" in log
    assert not host.starts
    for minutes in (10, 20):  # the halt's own check would pass here and resume a halt of a failed check
        clock.now = T0 + 60 * minutes
        poll()
    assert not host.starts and "halted" in json.loads(path.read_text())
    flag.unlink()
    clock.now = T0 + 60 * 21
    assert poll()
    assert "halted" not in json.loads(path.read_text()) and any("resumed at" in line and "the owner resumed" in line for line in log)
    assert [h for h in host.starts if "ABC-635" in str(h)], "the start goes on after the resume"
    assert notices == [None], "the pause writes no setup notice; the resume only closes one that an earlier halt left open"


def test_main_reads_the_pause_file_beside_its_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`main` wires the halt to `orchestrator.paused` beside the config it was given, so a pause reaches the service."""
    state = tmp_path / "state.json"
    config = tmp_path / "c.toml"
    config.write_text(TEST_TOML.replace('"~/.local/state/orchestrator/state.json"', f'"{state}"', 1))
    config.with_name(config.stem + ".paused").write_text("")
    assert orch.main(["--config", str(config), "--once"], reads_of([issue("ABC-635", area="docs")], []), host=FakeHost()) == 0
    assert json.loads(state.read_text())["halted"] == "paused by the owner"
    assert "halted: paused by the owner" in capsys.readouterr().out


def test_a_confirmed_launch_or_a_failed_confirmation_never_ends_the_owners_pause(tmp_path: Path) -> None:
    """While the pause file exists, no path clears the halt. A host that confirms a pending launch does
    not resume it, a confirmation that raises leaves a cause that `resume` still recognises, and the starts wait."""
    runner, host, writes, clock = runner_of(tmp_path)
    flag, log, path = tmp_path / "orchestrator.paused", [], tmp_path / "state.json"
    runner.halt.check = lambda: [(orch.OK, "preflight")]
    runner.halt.hold = flag.exists
    runner.writes = writes.writes()
    data = orch.Reads(lambda: [issue("ABC-635", area="docs")], list)

    def poll() -> bool:
        return orch.round_all([orch.Team(runner.cfg, data, runner)], log.append, dry=False, state_file=path)

    outcome: list[Any] = [[orch.Event("started", "ABC-9", "started ABC-9")]]

    def confirm() -> list[Any]:
        if isinstance(outcome[0], Exception):
            raise outcome[0]
        return outcome[0]

    runner.confirm_launch = confirm
    flag.write_text("")
    outcome[0] = RuntimeError("host down")  # the confirmation fails during the pause
    poll()
    assert json.loads(path.read_text())["halted"].startswith("paused by the owner")
    outcome[0] = [orch.Event("started", "ABC-9", "started ABC-9")]  # the host then confirms the session
    for minutes in (1, 2):
        clock.now = T0 + 60 * minutes
        poll()
        assert json.loads(path.read_text())["halted"] == "paused by the owner" and not host.starts
    flag.unlink()
    clock.now = T0 + 180
    poll()
    assert "halted" not in json.loads(path.read_text())


def test_the_owners_resume_keeps_the_halt_of_a_launch_that_the_host_still_cannot_confirm(tmp_path: Path) -> None:
    """A pause during a failing confirmation, then `resume` while it still fails: the starts stay halted
    with the confirmation's cause until the host answers, then the halt ends as before."""
    runner, host, writes, clock = runner_of(tmp_path)
    flag, log, path = tmp_path / "orchestrator.paused", [], tmp_path / "state.json"
    runner.halt.check = lambda: [(orch.OK, "preflight")]
    runner.halt.hold = flag.exists
    runner.writes = writes.writes()
    data = orch.Reads(lambda: [issue("ABC-635", area="docs")], list)
    answer: list[Any] = [RuntimeError("host down")]

    def confirm() -> list[Any]:
        if isinstance(answer[0], Exception):
            raise answer[0]
        return answer[0]

    runner.confirm_launch = confirm

    def poll(minutes: int) -> None:
        clock.now = T0 + 60 * minutes
        orch.round_all([orch.Team(runner.cfg, data, runner)], log.append, dry=False, state_file=path)

    flag.write_text("")
    poll(0)
    poll(1)
    assert json.loads(path.read_text())["halted"].startswith("paused by the owner")
    flag.unlink()
    for minutes in (2, 3):
        poll(minutes)
        assert "halted" in json.loads(path.read_text()) and not host.starts, "still halted: the launch is unconfirmed"
    answer[0] = [orch.Event("started", "ABC-9", "started ABC-9")]
    poll(4)
    assert "halted" not in json.loads(path.read_text())


# -- A switch of session_host needs no wait ------------------------------------------------------------------


def switched(tmp_path: Path, old: FakeHost, record: object = None, **changes: Any) -> tuple[Any, FakeHost, Any, Clock]:
    """A runner on the host in force `herdr` (a new FakeHost) whose record names a session of ABC-7 on the old host `paseo`."""
    clock, new = Clock(), FakeHost()
    if record is None:
        record = {"in_force": "paseo", "sessions": [{"host": "paseo", "key": "ABC-7", "id": "p1", "seen": stamp(T0 - 600)}]}
    hosts = orch.Hosts("herdr", new, {"paseo": old}.__getitem__, record, clock)
    writes = FakeWrites()
    return orch.Runner(fix_cfg(tmp_path, **changes), hosts, writes.writes(), clock), new, hosts, clock


def test_a_switch_wakes_the_old_hosts_session_there_and_starts_new_sessions_on_the_new_host(tmp_path: Path) -> None:
    old = FakeHost()
    old.wakeable, old.spaces = True, [{"workspace_id": "p1", "label": "ABC-7 ·", "agent_status": "idle"}]
    runner, new, hosts, clock = switched(tmp_path, old)
    clock.now = T0 + 60
    assert runner.fix_round([waiting_issue()], [pr(7, "abc-7-x", reviews=ASKED)], dry=False) == ["woke the session of ABC-7 (changes requested)"]
    assert old.wakes == [("ABC-7", orch.wake_text(7, "changes requested"))] and new.wakes == []
    runner.start_run("ABC-9", "start")
    assert [label for label, _, _ in new.starts] == ["ABC-9 ·"] and old.starts == []
    new.spaces = [{"workspace_id": "h1", "label": "ABC-9 ·", "agent_status": "working"}]
    assert [(s["host"], s["workspace_id"]) for s in hosts.workspaces()] == [("herdr", "h1"), ("paseo", "p1")]
    record = hosts.record()
    assert record["in_force"] == "herdr" and record["unreached"] == {}
    assert [(r["host"], r["key"], r["seen"]) for r in record["sessions"]] == [("herdr", "ABC-9", stamp(T0 + 60)), ("paseo", "ABC-7", stamp(T0 - 600))]
    # --watch names each session's host while two are in use
    assert orch.hosts_text({"hosts": record}) == ["herdr (new starts): ABC-9", "paseo (old host): ABC-7"]
    lines = orch.render({**VIEW_PLAN, "hosts": record}, T0, 60, 40, color=False)
    assert "  herdr (new starts): ABC-9" in lines and "  paseo (old host): ABC-7" in lines
    # the close of the old host's session goes to the old host
    hosts.close("p1")
    hosts.close("h1")
    assert old.closed == ["p1"] and new.closed == ["h1"]


def test_the_wake_goes_to_the_host_that_shows_the_keys_newest_session(tmp_path: Path) -> None:
    old = FakeHost()
    old.spaces = [{"workspace_id": "p1", "label": "ABC-7 ·", "agent_status": "idle"}]
    _, new, hosts, clock = switched(tmp_path, old)
    new.spaces = [{"workspace_id": "h1", "label": "ABC-7 ·", "agent_status": "idle"}]
    clock.now = T0
    hosts.workspaces()
    hosts.wake("ABC-7", "x")
    assert new.wakes == [("ABC-7", "x")] and old.wakes == [], "the session on the host in force is the newer one"
    # Paseo, then herdr, then Paseo again: the herdr session started after the old Paseo one, so it is the newest
    record = {"in_force": "herdr", "sessions": [{"host": "paseo", "key": "ABC-7", "id": "p1", "seen": stamp(T0 - 600)},
                                                {"host": "herdr", "key": "ABC-7", "id": "h1", "seen": stamp(T0 - 60)}]}
    back, herdr = FakeHost(), FakeHost()
    back.spaces, herdr.spaces = old.spaces, new.spaces
    hosts = orch.Hosts("paseo", back, {"herdr": herdr}.__getitem__, record, clock)
    hosts.workspaces()
    hosts.wake("ABC-7", "y")
    assert herdr.wakes == [("ABC-7", "y")] and back.wakes == []
    # a key with no recorded session goes to the host in force, which answers False as before
    assert hosts.wake("ABC-8", "z") is False and back.wakes == [("ABC-8", "z")]


def test_a_session_whose_host_cannot_be_reached_gets_a_new_session_on_the_host_in_force_after_the_claim_wait(tmp_path: Path) -> None:
    class Gone(FakeHost):
        def workspaces(self) -> list[dict[str, Any]]:
            raise RuntimeError("paseo ls failed: daemon not running")

        def paused(self) -> dict[str | None, tuple[str, str]]:
            raise RuntimeError("paseo ls failed: daemon not running")

    old = Gone()
    old.wake_error = RuntimeError("paseo ls failed: daemon not running")
    runner, new, hosts, clock = switched(tmp_path, old)
    issues, prs = [waiting_issue()], [pr(7, "abc-7-x", reviews=ASKED)]
    clock.now = T0 + 60
    assert runner.fix_round(issues, prs, dry=False) == ["wake of the session of ABC-7 failed: RuntimeError: paseo ls failed: daemon not running"]
    assert old.wakes == [("ABC-7", orch.wake_text(7, "changes requested"))] and hosts.record()["unreached"] == {"paseo": stamp(T0 + 60)}
    assert runner.pause_events() == []  # an old host with no answer pauses nothing
    clock.now = T0 + 300
    assert runner.fix_round(issues, prs, dry=False) == ["started a fix session for ABC-7 (claude-sonnet-5-5, medium)"]
    assert [label for label, _, _ in new.starts] == ["ABC-7 ·"] and old.starts == []
    # the record keeps the old session while its host does not answer, and the host leaves after `drop_after`
    assert [r["host"] for r in hosts.record()["sessions"]] == ["paseo"]
    clock.now = T0 + 60 + 12 * orch.HOUR
    hosts.workspaces()
    assert hosts.record() == {"in_force": "herdr", "sessions": [], "unreached": {}} and hosts.others == {}


def test_the_old_host_leaves_once_no_session_uses_it(tmp_path: Path) -> None:
    old = FakeHost()
    old.spaces = [{"workspace_id": "p1", "label": "ABC-7 ·", "agent_status": "done"},
                  {"workspace_id": "mine", "label": "the owner's own agent", "agent_status": "idle"}]
    _, new, hosts, _ = switched(tmp_path, old)
    hosts.workspaces()
    assert list(hosts.others) == ["paseo"] and orch.hosts_text({"hosts": hosts.record()}) == ["herdr (new starts): no session", "paseo (old host): ABC-7"]
    old.spaces = old.spaces[1:]  # its last session closed: the owner's own agent does not keep the host
    hosts.workspaces()
    assert hosts.others == {} and hosts.record()["sessions"] == [] and orch.hosts_text({"hosts": hosts.record()}) == []
    old.spaces = [{"workspace_id": "p2", "label": "ABC-8 ·", "agent_status": "idle"}]
    assert hosts.workspaces() == [] and hosts.wake("ABC-8", "x") is False and old.wakes == [], "a host that left is not read again"
    # a record with only the host in force makes no other host
    hosts = orch.Hosts("herdr", new, lambda name: pytest.fail(f"made {name}"), {"sessions": [{"host": "herdr", "key": "ABC-1", "id": "h", "seen": stamp(T0)}]})
    assert hosts.others == {}


def test_an_old_hosts_stop_counts_for_a_profile_that_the_host_in_force_has_none_of(tmp_path: Path) -> None:
    old = FakeHost()
    old.spaces = [{"workspace_id": "p1", "label": "ABC-7 ·", "agent_status": "done"}]
    old.pauses = {"claude": ("ABC-7", "usage limit"), "pi-ollama": ("ABC-7", "usage limit")}
    _, new, hosts, _ = switched(tmp_path, old)
    new.pauses = {"claude": ("ABC-9", "invalid key")}
    assert hosts.paused() == {"claude": ("ABC-9", "invalid key"), "pi-ollama": ("ABC-7", "usage limit")}


def test_the_service_reads_the_hosts_record_and_writes_it_back_each_round(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"hosts": {"in_force": "herdr", "sessions": [{"host": "herdr", "key": "ABC-1", "id": "w1", "seen": stamp(T0)}]}}))
    path = config_with(tmp_path, state_file=f'state_file = "{state}"', session_host='session_host = "herdr"')
    host = FakeHost()
    host.spaces = [{"workspace_id": "w1", "label": "ABC-1 ·", "agent_status": "idle"}, {"workspace_id": "w2", "label": "ABC-2 ·", "agent_status": "working"}]
    assert orch.main(["--config", str(path), "--once"], reads_of([], []), host=host) == 0
    hosts = json.loads(state.read_text())["hosts"]
    assert hosts["in_force"] == "herdr" and [(r["key"], r["id"]) for r in hosts["sessions"]] == [("ABC-1", "w1"), ("ABC-2", "w2")]
    assert hosts["sessions"][0]["seen"] == stamp(T0), "a session keeps the time the record first saw it"
    assert orch.read_record(tmp_path / "missing.json") is None


def test_a_restart_keeps_the_record_of_an_old_host_with_no_answer_and_drops_rows_it_cannot_read() -> None:
    record = {"in_force": "herdr", "unreached": {"paseo": stamp(T0 - 3600), "herdr": stamp(T0), "process": stamp(T0)},
              "sessions": [{"host": "paseo", "key": "ABC-7", "id": "p1", "seen": stamp(T0 - 7200)},
                           {"host": "process", "key": "ABC-8", "id": "x", "seen": stamp(T0)},  # a plain process lists nothing
                           {"host": "ssh", "key": "ABC-9", "id": "y", "seen": stamp(T0)},
                           {"host": "paseo", "key": "", "id": "p2", "seen": stamp(T0)}, {"host": "paseo", "key": "ABC-6", "id": "p3"}, "row"]}
    made: list[str] = []
    hosts = orch.Hosts("herdr", FakeHost(), lambda name: made.append(name) or FakeHost(), record, lambda: T0)
    assert made == ["paseo"] and hosts.record() == {"in_force": "herdr", "unreached": {"paseo": stamp(T0 - 3600)},
                                                     "sessions": [{"host": "paseo", "key": "ABC-7", "id": "p1", "seen": stamp(T0 - 7200)}]}
    assert orch.Hosts("herdr", FakeHost(), pytest.fail, ["not a record"]).record()["sessions"] == []


def test_the_service_keeps_no_daemon_and_cleans_no_start_of_an_old_paseo_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With herdr in force, the old Paseo host is only read: no PaseoDaemon (which stops and starts Paseo's daemon), and no
    archive of `clean_failed_starts`."""
    monkeypatch.setattr(orch, "PaseoDaemon", lambda *a, **k: pytest.fail("a daemon for an old Paseo host"))
    calls: list[list[str]] = []

    def run(argv: Sequence[str]) -> Any:
        calls.append(list(argv))
        return orch.subprocess.CompletedProcess(argv, 0, json.dumps([{"id": "p1", "name": "ABC-7 ·", "status": "running"}]))
    old, new = orch.PaseoHost(run=run), FakeHost()
    old.empty_starts.add("ws1")
    monkeypatch.setattr(orch, "make_host", lambda cfg: new if cfg.session_host == "herdr" else old)
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"hosts": {"in_force": "paseo", "sessions": [{"host": "paseo", "key": "ABC-7", "id": "p1", "seen": stamp(T0)}]}}))
    path = config_with(tmp_path, state_file=f'state_file = "{state}"', session_host='session_host = "herdr"', dry_run="dry_run = false")
    assert orch.main(["--config", str(path), "--once"], reads_of([], []), writes=FakeWrites().writes(), probes=probes()) == 0
    assert calls and all(argv[:2] == ["paseo", "ls"] for argv in calls) and old.empty_starts == {"ws1"}
    assert [(r["host"], r["key"]) for r in json.loads(state.read_text())["hosts"]["sessions"]] == [("paseo", "ABC-7")]
    assert orch.primary_of(orch.Hosts("herdr", new, lambda name: old)) is new and orch.primary_of(old) is old


def test_check_lists_the_sessions_of_an_old_host_and_names_its_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """after a switch of `session_host`, a session of the old host that works for a day gets its WARN, with its host."""
    spaces = [{"workspace_id": "p1", "label": "ABC-7 ·", "agent_status": "working", "host": "paseo"},
              {"workspace_id": "h1", "label": "ABC-8 ·", "agent_status": "working", "host": "herdr"}]
    assert orch.check_sessions(spaces, NOW, {"ABC-7": NOW - 25 * HOUR, "ABC-8": NOW - 25 * HOUR}.__getitem__, "herdr") == [
        ("WARN", "sessions: ABC-7 (on paseo) still works, 25 hours after its start"),
        ("WARN", "sessions: ABC-8 still works, 25 hours after its start")]
    # the real probe: the state file's record brings the old host into the list
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"hosts": {"sessions": [{"host": "paseo", "key": "ABC-7", "id": "p1", "seen": stamp(T0)}]}}))
    cfg = dataclasses.replace(orch.load_config(config_with(tmp_path, session_host="session_host = 'herdr'")), state_file=str(state))
    old, new = FakeHost(), FakeHost()
    old.spaces = [spaces[0]]
    made: list[str] = []
    monkeypatch.setattr(orch, "make_host", lambda c: made.append(c.session_host) or (new if c.session_host == "herdr" else old))
    listed = orch.old_hosts_too(cfg).workspaces()
    assert made == ["herdr", "paseo"] and [s["host"] for s in listed] == ["paseo"]


def test_check_reads_an_old_hosts_sessions_when_process_is_in_force(tmp_path: Path) -> None:
    """A switch to `process` leaves the old host's stuck session with its WARN."""
    process = orch.load_config(config_with(tmp_path, session_host="session_host = 'process'"))
    old = [{"workspace_id": "h1", "label": "ABC-7 ·", "agent_status": "working", "host": "herdr"}]
    said: list[str] = []
    orch.run_check(process, probes(sessions=old, started=lambda key: NOW - 25 * HOUR), said.append)
    assert "WARN sessions: ABC-7 (on herdr) still works, 25 hours after its start" in said


# -- OWL-34: each run gets its share of the Mac's CPUs for `pytest -n auto` ------------------------------------

def test_a_run_gets_the_cpu_count_divided_by_max_runs_and_at_least_two() -> None:
    assert orch.pytest_workers(2, cpus=14) == 7
    assert orch.pytest_workers(1, cpus=14) == 14
    assert orch.pytest_workers(2, cpus=2) == 2
    assert orch.pytest_workers(3, cpus=4) == 2  # the floor
    assert orch.pytest_workers(0, cpus=4) == 4  # a bad `max_runs` never divides by zero


@pytest.mark.parametrize(("host", "cpus", "max_runs", "want"), [
    ("paseo", 14, 2, " --env PYTEST_XDIST_AUTO_NUM_WORKERS=7 "),
    ("paseo", 14, 1, " --env PYTEST_XDIST_AUTO_NUM_WORKERS=14 "),
    ("paseo", 2, 2, " --env PYTEST_XDIST_AUTO_NUM_WORKERS=2 "),
    ("herdr", 14, 2, " --workers 7 "),
    ("process", 2, 2, " --workers 2 "),
])
def test_a_started_session_gets_its_pytest_workers(tmp_path: Path, monkeypatch, host: str, cpus: int, max_runs: int, want: str) -> None:
    monkeypatch.setattr(orch.os, "cpu_count", lambda: cpus)
    cfg = dataclasses.replace(orch.load_config(config_with(tmp_path, session_host=f'session_host = "{host}"')), max_runs=max_runs)
    model, effort, profile = orch.run_settings([], cfg)
    command = orch.format_run(cfg, "ABC-7", "start", model, effort, "ABC-7 ·", str(tmp_path), "20261126T004913Z-mini", profile)
    assert want in command
    assert want in orch.check_command(cfg)[1]


def test_the_run_line_names_the_workers_only_when_the_start_gave_them(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(orch.os, "cpu_count", lambda: 14)
    assert orch.run_line("m", "high", ("r", "low"), "20261126T004913Z-mini", "claude", 7) == (
        "Run: m, effort high; review r, effort low; profile claude; pytest workers 7 (start 20261126T004913Z-mini)")
    assert orch.run_line("m", "high") == "Run: m, effort high"
    assert orch.fix_run_line("m", "high", ("r", "low"), 7, "claude", 7).endswith("; pytest workers 7 (fix round of PR #7)")
    runner, _, _, _ = runner_of(tmp_path, max_runs=2, run_command="run-it {key} {mode}")
    assert runner.workers_given("", "high") == 0  # no place for it in the command: the session got none
    runner, _, _, _ = runner_of(tmp_path, max_runs=2, run_command="claude --profile {profile}{workers_flag} {key}")
    assert runner.workers_given("", "high") == 7
    assert "pytest workers 7" in runner.token_line("m", "high", "20261126T004913Z-mini")


def test_a_bad_worker_count_is_refused() -> None:
    with pytest.raises(ValueError):
        orch.command_for("x {key}", "ABC-7", "start", workers=-1)
    assert orch.command_for("x {key}{workers_flag}{workers_env}", "ABC-7", "start") == "x ABC-7"


def test_check_home_copy_of_agent_user_warns_when_it_differs(tmp_path: Path) -> None:
    installed = tmp_path / "installed.py"
    installed.write_bytes(b"new")
    (tmp_path / "agent_user.py").write_bytes(b"old")
    line = orch.read_home_copy(tmp_path, installed)
    assert line == (orch.WARN, "agent_user.py: the agents' home copy differs from the installed one, so a pane's claude or pi can "
                    "start with old options: run owlcode admin from a checkout at the installed commit, or owlcode install "
                    "when the checkout is newer")
    (tmp_path / "agent_user.py").write_bytes(b"new")
    assert orch.read_home_copy(tmp_path, installed) == (orch.OK, "agent_user.py: the home copy is the installed one")
    (tmp_path / "agent_user.py").unlink()
    assert orch.read_home_copy(tmp_path, installed) is None  # no home copy: no line


def test_run_check_shows_the_home_copy_line_and_never_fails_on_it() -> None:
    p = dataclasses.replace(probes(), home_copy=lambda: (orch.WARN, "agent_user.py: the agents' home copy differs"))
    code, said = check(p)
    assert code == 0 and "WARN agent_user.py: the agents' home copy differs" in said

    def boom() -> None:
        raise PermissionError("x")

    code, said = check(dataclasses.replace(p, home_copy=boom))
    assert code == 0 and any("cannot compare the home copy (PermissionError)" in line for line in said)


def test_a_slow_round_shows_in_the_state_file_beside_the_last_plan_and_goes_away_with_its_plan(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    orch.write_state(path, VIEW_PLAN)
    seen: list[dict[str, Any]] = []

    def slow_issues() -> Any:
        seen.append(orch.json.loads(path.read_text()))  # what `--watch` reads while the round reads Linear
        return []

    assert orch.round_once(CFG, orch.Reads(issues=slow_issues, prs=lambda: []), state_file=path) is True
    assert seen[0]["time"] == VIEW_PLAN["time"] and "round_started" in seen[0], "the last plan stays, with the round's start"
    assert "round_started" not in orch.json.loads(path.read_text()), "the round's own plan replaces the file"


def test_watch_and_check_say_that_a_round_runs_and_since_when() -> None:
    running = dict(VIEW_PLAN, round_started="2026-11-10T18:30:12Z")  # newer than the plan
    now = VIEW_NOW + 6 * 60
    lines = orch.render_all(running, now, 80, 40, False, UTC)
    assert lines[0] == "round running for 6 min (since 18:30)", lines
    assert not any("round running" in line for line in orch.render_all(VIEW_PLAN, now, 80, 40, False, UTC)), "no mark, no line"
    assert not any("round running" in line for line in orch.render_all(dict(running, time="2026-11-10T18:31:00Z"), now, 80, 40, False, UTC))
    state = orch.json.dumps(running)
    assert orch.check_round(state, now) is not None and orch.check_round(state, now)[0] == orch.OK
    assert "round running for 6 min" in orch.check_round(state, now)[1]  # type: ignore[index]
    assert orch.check_round(orch.json.dumps(VIEW_PLAN), now) is None and orch.check_round(None, now) is None


def test_a_round_mark_of_a_gone_process_shows_no_running_round(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orch, "_process_start", lambda pid: None)  # the marks below are older than this process
    import subprocess
    import sys
    done = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"], capture_output=True, text=True, check=True)
    gone = int(done.stdout)  # a process that ended
    now = VIEW_NOW + 6 * 60
    mark = dict(VIEW_PLAN, round_started="2026-11-10T18:30:12Z")
    assert orch.running_text(dict(mark, round_pid=os.getpid()), now) is not None, "its process lives"
    assert orch.running_text(dict(mark, round_pid=gone), now) is None, "its process is gone"
    assert orch.check_round(orch.json.dumps(dict(mark, round_pid=gone)), now) is None
    assert not any("round running" in line for line in orch.render_all(dict(mark, round_pid=gone), now, 80, 40, False, UTC))
    path = tmp_path / "state.json"
    orch.mark_round_start(path, VIEW_NOW)
    assert orch.json.loads(path.read_text())["round_pid"] == os.getpid()


def test_a_round_mark_whose_pid_a_newer_process_holds_shows_no_running_round(monkeypatch: pytest.MonkeyPatch) -> None:
    started = orch._utc("2026-11-10T18:30:12Z", "%Y-%m-%dT%H:%M:%SZ")
    mark = dict(VIEW_PLAN, round_started="2026-11-10T18:30:12Z", round_pid=os.getpid())
    now = VIEW_NOW + 360
    monkeypatch.setattr(orch, "_process_start", lambda pid: started - 3600)  # the round's own process: older than its mark
    assert orch.running_text(mark, now) is not None
    monkeypatch.setattr(orch, "_process_start", lambda pid: started + 3600)  # a process after a reboot with the old pid
    assert orch.running_text(mark, now) is None
    monkeypatch.setattr(orch, "_process_start", lambda pid: None)  # `ps` cannot say: as before
    assert orch.running_text(mark, now) is not None


def test_the_process_start_reads_ps_for_a_live_process_and_none_for_a_gone_one() -> None:
    begun = orch._process_start(os.getpid())
    assert begun is not None and begun > 0
    assert orch._process_start(2**22 + 12345) is None


def test_the_process_start_is_read_in_utc(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_run(cmd: Any, **kwargs: Any) -> Any:
        seen.update(kwargs)
        return orch.subprocess.CompletedProcess(cmd, 0, stdout="Tue Nov 10 18:30:12 2026\n", stderr="")

    monkeypatch.setattr(orch.subprocess, "run", fake_run)
    assert orch._process_start(123) == orch._utc("2026-11-10T18:30:12Z", "%Y-%m-%dT%H:%M:%SZ")
    assert seen["env"]["TZ"] == "UTC0" and seen["env"]["LC_ALL"] == "C"


def test_a_round_of_another_user_counts_as_running(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(pid: int, sig: int) -> None:
        raise PermissionError

    monkeypatch.setattr(orch.os, "kill", denied)
    monkeypatch.setattr(orch, "_process_start", lambda pid: None)
    mark = dict(VIEW_PLAN, round_started="2026-11-10T18:30:12Z", round_pid=1)
    assert orch.running_text(mark, VIEW_NOW + 360) is not None


def test_check_prints_the_round_that_runs_beside_the_last_round() -> None:
    code, said = check(probes(state=orch.json.dumps({"version": 1, "time": "2026-11-10T11:59:00Z", "round_started": "2026-11-10T11:59:30Z"})))
    assert code == 0 and "ok   the service: last round at 2026-11-10T11:59:00Z" in said
    assert any(line.startswith("ok   the service: round running for 30 s (since ") for line in said), said


def test_the_round_start_mark_survives_a_bad_state_file_and_a_failed_write(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("[1]")  # not a plan
    orch.mark_round_start(path, VIEW_NOW)
    assert list(orch.json.loads(path.read_text())) == ["round_started", "round_pid"]
    assert orch.round_once(CFG, reads_of([], []), state_file=tmp_path / "none" / "state.json") is True
    (tmp_path / "file").write_text("")
    assert orch.round_once(CFG, reads_of([], []), state_file=tmp_path / "file" / "state.json") is False  # the end write reports it
