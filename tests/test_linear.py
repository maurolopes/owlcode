"""The Linear script the runs use (.github/linear.py): the key from a branch name, the API key's two sources, and each
command's requests and replies, against a fake that answers by the operation's name (never by arrival order)."""

import importlib.util
import json
import re
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.conftest import REAL_REPO


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("github_linear", REAL_REPO / ".github" / "linear.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


linear = load()

ISSUE = {"id": "uuid-12", "identifier": "ABC-12", "title": "Hide stale postings", "description": "the text", "url": "https://linear.app/x/ABC-12",
         "state": {"name": "In Review", "type": "started"}, "team": {"id": "team-uuid", "key": "ABC"}, "labels": {"nodes": [{"name": "follow-up"}]}}
STATES = [{"id": "s-triage", "name": "Triage", "type": "triage", "position": 0}, {"id": "s-todo", "name": "Todo", "type": "unstarted", "position": 1},
          {"id": "s-progress", "name": "In Progress", "type": "started", "position": 2}, {"id": "s-nd", "name": "Escalated", "type": "started", "position": 4},
          {"id": "s-approved", "name": "Approved", "type": "started", "position": 5}]


# a team before `setup`: the default states, one automation to change and one that is right, the triage inbox off
BARE = {"id": "team-uuid", "key": "ABC", "triageEnabled": False,
        "states": {"nodes": [{"id": "s-done", "name": "Done", "type": "completed", "position": 3}, {"id": "s-todo", "name": "Todo", "type": "unstarted", "position": 1},
                             {"id": "s-progress", "name": "In Progress", "type": "started", "position": 2},
                             {"id": "s-canceled", "name": "Canceled", "type": "canceled", "position": 4}]},
        "gitAutomationStates": {"nodes": [{"id": "a-start", "event": "start", "state": {"id": "s-progress", "name": "In Progress"}, "targetBranch": None},
                                          {"id": "a-review", "event": "review", "state": {"id": "s-progress", "name": "In Progress"}, "targetBranch": None},
                                          {"id": "a-merge", "event": "merge", "state": {"id": "s-done", "name": "Done"}, "targetBranch": None},
                                          {"id": "a-main", "event": "start", "state": {"id": "s-done", "name": "Done"}, "targetBranch": {"id": "b-main"}}]}}
# the same team after it: every state, every automation as wanted, the inbox on
READY = {"id": "team-uuid", "key": "ABC", "triageEnabled": True,
         "states": {"nodes": [*STATES, {"id": "s-review", "name": "In Review", "type": "started", "position": 3}, {"id": "s-done", "name": "Done", "type": "completed", "position": 6}]},
         "gitAutomationStates": {"nodes": [{"id": "a-draft", "event": "draft", "state": {"id": "s-progress", "name": "In Progress"}, "targetBranch": None},
                                           {"id": "a-start", "event": "start", "state": {"id": "s-review", "name": "In Review"}, "targetBranch": None},
                                           {"id": "a-merge", "event": "merge", "state": {"id": "s-done", "name": "Done"}, "targetBranch": None}]}}

USERS = [{"id": "u-owner", "name": "Mauro Lopes", "email": "owner@example.com"}, {"id": "u-a", "name": "Alex", "email": "alex@example.com"},
         {"id": "u-b", "name": "Alex", "email": "alex.b@example.com"}]


class Fake:
    """Answers by the operation named in the query; records every call."""

    def __init__(self, history: list[dict[str, Any]] | None = None, issue: dict[str, Any] | None = ISSUE,
                 team: dict[str, Any] | None = BARE, labels: list[dict[str, Any]] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.history = history or []
        self.issue = issue
        self.team = team
        self.pages: list[list[dict[str, Any]]] = [[]]
        self.on_append: list[Callable[[str], str]] = []
        # the pages of one issue's connection after its first: (issue key, connection) -> the pages, cursors "1", "2", ...
        self.more: dict[tuple[str, str], list[list[dict[str, Any]]]] = {}
        self.projects: list[dict[str, Any]] = [{"id": "p-workflow", "name": "Agent workflow", "url": "https://linear.app/x/project/aw"}]
        self.labels = labels if labels is not None else [{"id": "l-fu", "name": "follow-up", "team": None}, {"id": "l-other", "name": "hands-off", "team": {"key": "OTHER"}}]

    def __call__(self, query: str, variables: Any) -> dict[str, Any]:
        name = query.split("(")[0].split("{")[0].split()[-1]
        self.calls.append((name, dict(variables)))
        if name == "Issue":
            return {"issue": self.issue}
        if name == "Setup":
            return {"teams": {"nodes": [self.team] if self.team else []}}
        if name == "StateCreate":
            return {"workflowStateCreate": {"success": True, "workflowState": {"id": "s-new-" + variables["input"]["name"]}}}
        if name == "LabelCreate":
            return {"issueLabelCreate": {"success": True, "issueLabel": {"id": "l-new-" + variables["input"]["name"]}}}
        if name in ("AutomationDelete", "AutomationCreate", "TeamUpdate", "Unlabel", "Label"):
            field = {"Unlabel": "issueRemoveLabel", "Label": "issueAddLabel", "AutomationDelete": "gitAutomationStateDelete", "AutomationCreate": "gitAutomationStateCreate", "TeamUpdate": "teamUpdate"}[name]
            return {field: {"success": True}}
        if name == "Team":
            return {"teams": {"nodes": [{"id": "team-uuid", "key": "ABC", "states": {"nodes": list(reversed(STATES))}}]}}
        if name == "Move":
            state = next(s["name"] for s in STATES if s["id"] == variables["state"])
            return {"issueUpdate": {"success": True, "issue": {"state": {"name": state}}}}
        if name == "Comment":
            return {"commentCreate": {"success": True}}
        if name == "Labels":
            return {"issueLabels": {"nodes": self.labels}}
        if name == "Create":
            return {"issueCreate": {"success": True, "issue": {"id": "uuid-40", "identifier": "ABC-40", "url": "https://linear.app/x/ABC-40"}}}
        if name == "Viewer":
            return {"viewer": {"id": "u-owner"}}
        if name == "Users":
            return {"users": {"nodes": [u for u in USERS if variables["who"].casefold() in (u["name"].casefold(), u["email"])]}}
        if name == "Relate":
            return {"issueRelationCreate": {"success": True}}
        if name in ("History", "Entered"):
            return {"issue": {"history": {"nodes": self.history}}}
        if name == "Issues":
            page = self.pages[int(variables["after"] or 0)]
            more = int(variables["after"] or 0) + 1 < len(self.pages)
            return {"issues": {"nodes": page, "pageInfo": {"hasNextPage": more, "endCursor": str(int(variables["after"] or 0) + 1)}}}
        if name == "More":
            connection = query.split("{ issue(id: $id) { ")[1].split("(")[0]
            pages, at = self.more[(variables["id"], connection)], int(variables["after"])
            more = at < len(pages)
            return {"issue": {connection: {"nodes": pages[at - 1], "pageInfo": {"hasNextPage": more, "endCursor": str(at + 1)}}}}
        if name == "Append" and self.issue and variables["id"] == self.issue["id"]:
            # Linear keeps the text written; `on_append` writes another review's text after it
            self.issue = {**self.issue, "description": variables["input"]["description"]}
            if self.on_append:
                self.issue = {**self.issue, "description": self.on_append.pop(0)(self.issue["description"])}
        if name in ("Describe", "Append"):
            return {"issueUpdate": {"success": True}}
        if name == "Set":
            return {"issueUpdate": {"success": True}}
        if name == "Projects":
            return {"projects": {"nodes": [p for p in self.projects if p["name"].casefold() == variables["name"].casefold()]}}
        if name == "ProjectCreate":
            return {"projectCreate": {"success": True, "project": {"name": variables["input"]["name"], "url": "https://linear.app/x/project/new"}}}
        raise AssertionError(name)


def test_the_key_comes_from_a_branch_name_or_a_title():
    assert linear.issue_key("abc-12-hide-stale-postings") == "ABC-12"
    assert linear.issue_key("mauro/abc-7-x", "ignored") == "ABC-7"
    assert linear.issue_key("review-loop", "Review loop: ABC-120 and more") == "ABC-120"
    assert linear.issue_key("agent-workflow-2", "no key here") is None
    assert linear.issue_key("sha-256-digest") == "SHA-256", "a false key is possible: the lookup tells it apart (no such issue)"


def test_the_api_key_comes_from_the_environment_and_never_from_a_file(tmp_path, monkeypatch):
    # the owner's rule (2026-10-24): no key in a file on the owner's machine, so a key in a file is never read
    for place in (tmp_path / ".env", tmp_path / ".linear", tmp_path / ".config" / "linear" / "key"):
        place.parent.mkdir(parents=True, exist_ok=True)
        place.write_text("LINEAR_API_KEY=lin_file\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("builtins.open", lambda *a, **k: pytest.fail(f"opened {a}"))
    with pytest.raises(linear.Failed):
        linear.api_key({})
    assert linear.api_key({"LINEAR_API_KEY": " lin_env "}) == "lin_env"


class Transport:
    """A fake of urllib's urlopen: it keeps the Authorization header of each request and answers with an empty page of
    issues (the key a call sends, with no live call)."""

    def __init__(self) -> None:
        self.keys: list[str] = []

    def __call__(self, request: Any, timeout: float = 0) -> Any:
        self.keys.append(request.get_header("Authorization"))
        body = json.dumps({"data": {"issues": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}}}).encode()

        class Reply:
            def __enter__(self) -> Reply:
                return self

            def __exit__(self, *exc: object) -> None:
                return None

            def read(self) -> bytes:
                return body

        return Reply()


KEYS = {"LINEAR_API_KEY": "lin_api_main", "XYZ_LINEAR_API_KEY": "lin_api_xyz"}


@pytest.fixture
def own_key_team(monkeypatch):
    """A team of another Linear workspace: the neutral team XYZ names its own variable in TEAM_KEYS."""
    monkeypatch.setitem(linear.TEAM_KEYS, "XYZ", "XYZ_LINEAR_API_KEY")


def test_the_team_owl_and_the_other_teams_of_this_workspace_send_linear_api_key(monkeypatch, capsys):
    """OWL is a team of this workspace, so `list OWL` sends LINEAR_API_KEY, as `list ABC` and `list XYZ` do,
    whatever the case of the team's name."""
    sent = Transport()
    monkeypatch.setattr(linear.urllib.request, "urlopen", sent)
    assert linear.TEAM_KEYS == {}
    for team in ("OWL", "owl", "ABC", "XYZ"):
        sent.keys.clear()
        assert linear.run(["list", team, "--state", "Todo"], environ=KEYS) == 0, capsys.readouterr().err
        assert sent.keys == ["lin_api_main"], team


def test_a_team_with_its_own_key_sends_that_key_and_the_others_send_the_default(monkeypatch, capsys, own_key_team):
    """A product in another workspace names its variable in TEAM_KEYS: `list XYZ` sends it."""
    sent = Transport()
    monkeypatch.setattr(linear.urllib.request, "urlopen", sent)
    for team, key in (("XYZ", "lin_api_xyz"), ("xyz", "lin_api_xyz"), ("ABC", "lin_api_main"), ("OWL", "lin_api_main")):
        sent.keys.clear()
        assert linear.run(["list", team, "--state", "Todo"], environ=KEYS) == 0, capsys.readouterr().err
        assert sent.keys == [key], team


def test_an_issue_key_s_prefix_names_its_team_and_a_command_with_no_team_keeps_the_default(monkeypatch, own_key_team):
    """`state XYZ-3 Done` names XYZ by its key; a command that names no team (`key-from` sends nothing, and a
    key that is no issue key names none) takes LINEAR_API_KEY."""
    team = linear.command_team
    parse = linear.parser().parse_args
    assert team(parse(["state", "xyz-3", "Done"])) == "XYZ" and team(parse(["comment", "ABC-12", "--body-file", "x"])) == "ABC"
    assert team(parse(["link", "XYZ-3", "--related", "XYZ-4"])) == "XYZ" and team(parse(["triage-checks", "xyz"])) == "XYZ"
    assert team(parse(["list", "DEF", "--key", "DEF-1"])) == "DEF", "the --key list of `list` is no team"
    assert team(parse(["issue", "not a key"])) is None
    assert linear.key_variable("XYZ") == "XYZ_LINEAR_API_KEY"
    assert linear.key_variable("ABC") == linear.key_variable("OWL") == linear.key_variable(None) == "LINEAR_API_KEY"


def test_a_missing_team_key_fails_with_its_variable_and_sends_nothing(monkeypatch, capsys, own_key_team):
    """With no XYZ_LINEAR_API_KEY, `list XYZ` fails and names the variable; it never sends the other workspace's
    key in its place."""
    sent = Transport()
    monkeypatch.setattr(linear.urllib.request, "urlopen", sent)
    assert linear.run(["list", "XYZ"], environ={"LINEAR_API_KEY": "lin_api_main"}) == 1
    said = capsys.readouterr().err
    assert "no XYZ_LINEAR_API_KEY in the environment" in said and "The teams" in said and "lin_api" not in said
    assert linear.run(["state", "XYZ-3", "Done"], environ={"LINEAR_API_KEY": "lin_api_main", "XYZ_LINEAR_API_KEY": " "}) == 1
    assert sent.keys == []
    assert linear.run(["list", "ABC", "--state", "Todo"], environ={"LINEAR_API_KEY": "lin_api_main"}) == 0
    assert sent.keys == ["lin_api_main"], "a team's missing key does not stop the other teams"
    with pytest.raises(linear.Failed, match=r"^no LINEAR_API_KEY in the environment$"):
        linear.api_key({"XYZ_LINEAR_API_KEY": "lin_api_xyz"}, "ABC")


def test_the_team_keys_agree_with_the_teams_table():
    """AGENTS.md "The teams" names each team's key variable; linear.py's TEAM_KEYS holds the same."""
    table = (REAL_REPO / "AGENTS.md").read_text().split("\n## The teams\n", 1)[1].split("\n## ", 1)[0]
    rows = re.findall(r"^\| ([A-Z]+) \| [\w.-]+/[\w.-]+ \|.*\| `([A-Z_]+)` \|$", table, re.MULTILINE)
    assert len(rows) == len(re.findall(r"^\| [A-Z]+ \| ", table, re.MULTILINE)) >= 1, "each row ends with its key variable"
    assert {team: linear.key_variable(team) for team, _ in rows} == dict(rows)
    assert set(linear.TEAM_KEYS) <= {team for team, _ in rows}, "a team of TEAM_KEYS that the table does not name"


def test_issue_prints_what_the_reviewer_reads(capsys):
    fake = Fake()
    assert linear.run(["issue", "abc-12"], post=fake) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["identifier"] == "ABC-12" and out["state"] == "In Review" and out["labels"] == ["follow-up"] and out["team"] == "ABC"
    assert fake.calls == [("Issue", {"id": "abc-12"})]


def test_a_missing_issue_fails_loud(capsys):
    assert linear.run(["issue", "ABC-999"], post=Fake(issue=None)) == 1
    assert "no issue ABC-999" in capsys.readouterr().err


def test_state_moves_the_issue_to_the_named_state_of_its_team(capsys):
    fake = Fake()
    assert linear.run(["state", "ABC-12", "escalated"], post=fake) == 0
    assert capsys.readouterr().out.strip() == "Escalated"
    assert [c[0] for c in fake.calls] == ["Issue", "Team", "Move"]
    assert fake.calls[-1][1] == {"id": "uuid-12", "state": "s-nd"}, "the issue's uuid and the state's id, not names"
    assert linear.run(["state", "ABC-12", "Shipped"], post=Fake()) == 1
    assert "no state 'Shipped'" in capsys.readouterr().err


def test_comment_posts_the_file(tmp_path: Path):
    body = tmp_path / "q.md"
    body.write_text("Escalated: which host?")
    fake = Fake()
    assert linear.run(["comment", "ABC-12", "--body-file", str(body)], post=fake) == 0
    assert fake.calls[-1] == ("Comment", {"id": "uuid-12", "body": "Escalated: which host?"})


def test_create_files_a_follow_up_in_triage_with_the_label(tmp_path: Path, capsys):
    body = tmp_path / "f.md"
    body.write_text("From: PR #80, round 1\n- [ ] R1-3 app/x.py:10 a test is missing")
    fake = Fake()
    assert linear.run(["create", "ABC", "--title", "Follow-ups of PR #80", "--body-file", str(body), "--label", "follow-up"], post=fake) == 0
    assert capsys.readouterr().out.strip() == "ABC-40 https://linear.app/x/ABC-40"
    create = fake.calls[-1][1]["input"]
    assert create == {"teamId": "team-uuid", "title": "Follow-ups of PR #80", "description": body.read_text(), "stateId": "s-triage", "labelIds": ["l-fu"]}
    assert linear.run(["create", "ABC", "--title", "t", "--body-file", str(body), "--label", "hands-off"], post=Fake()) == 1
    assert "no label 'hands-off' for team ABC" in capsys.readouterr().err, "another team's label is not this team's"
    fake = Fake()
    assert linear.run(["create", "ABC", "--title", "t", "--body-file", str(body), "--state", "Todo"], post=fake) == 0
    assert fake.calls[-1][1]["input"]["stateId"] == "s-todo" and "labelIds" not in fake.calls[-1][1]["input"]


def test_create_files_a_fast_lane_issue_in_todo_with_its_area(tmp_path: Path, capsys):
    """agents/README.md "The fast lane": an agent's issue that passes the test is created in Todo, labelled `fast-lane` and
    with its Area label."""
    assert "fast-lane" in linear.LABELS, "setup makes the label the fast lane needs"
    body = tmp_path / "f.md"
    body.write_text("From: PR #80, round 1, abc1234, depth 1\n- [ ] R1-1 web/src/x.svelte:3 a check is missing")
    fake = Fake(labels=[{"id": "l-fu", "name": "follow-up", "team": None}, {"id": "l-fl", "name": "fast-lane", "team": {"key": "ABC"}},
                        {"id": "l-fe", "name": "frontend", "team": {"key": "ABC"}, "parent": {"name": "Area"}}])
    argv = ["create", "ABC", "--title", "Follow-ups of PR #80", "--body-file", str(body), "--state", "Todo",
            "--label", "follow-up", "--label", "frontend", "--label", "fast-lane"]
    assert linear.run(argv, post=fake) == 0
    create = fake.calls[-1][1]["input"]
    assert create["stateId"] == "s-todo" and sorted(create["labelIds"]) == ["l-fe", "l-fl", "l-fu"]
    capsys.readouterr()


def test_create_sets_the_priority_and_the_size(tmp_path: Path, capsys):
    body = tmp_path / "f.md"
    body.write_text("From: PR #80, round 1")
    fake = Fake()
    assert linear.run(["create", "ABC", "--title", "t", "--body-file", str(body), "--priority", "high", "--size", "L"], post=fake) == 0
    sent = fake.calls[-1][1]["input"]
    assert (sent["priority"], sent["estimate"]) == (2, 5), "Linear's numbers: High is 2, the T-shirt L is 5"
    fake = Fake()
    assert linear.run(["create", "ABC", "--title", "t", "--body-file", str(body)], post=fake) == 0
    assert "priority" not in fake.calls[-1][1]["input"] and "estimate" not in fake.calls[-1][1]["input"]
    with pytest.raises(SystemExit):
        linear.run(["create", "ABC", "--title", "t", "--body-file", str(body), "--size", "XL"], post=Fake())
    assert "invalid choice: 'XL'" in capsys.readouterr().err


def test_create_files_a_hands_off_issue_assigned_and_linked(tmp_path: Path, capsys):
    """agents/implementer.md "When you are done" step 5: the owner's live steps, assigned to the owner and related to the
    issue; a task that must come first blocks the issue ("While you work")."""
    body = tmp_path / "h.md"
    body.write_text("From: ABC-12\n- [ ] try the sign-in email in a real inbox")
    fake = Fake()
    assert linear.run(["create", "ABC", "--title", "t", "--body-file", str(body), "--label", "follow-up", "--assignee", "me", "--related", "ABC-12"], post=fake) == 0
    names = [name for name, _ in fake.calls]
    assert names.index("Create") < names.index("Relate"), "the relation needs the new issue's id"
    assert dict(fake.calls)["Create"]["input"]["assigneeId"] == "u-owner", "`me` is the API key's user"
    assert fake.calls[-1] == ("Relate", {"input": {"issueId": "uuid-40", "relatedIssueId": "uuid-12", "type": "related"}})
    fake = Fake()
    assert linear.run(["create", "ABC", "--title", "t", "--body-file", str(body), "--assignee", "MAURO LOPES", "--blocks", "ABC-12"], post=fake) == 0
    assert dict(fake.calls)["Create"]["input"]["assigneeId"] == "u-owner", "a name matches with the case ignored"
    assert fake.calls[-1][1]["input"]["type"] == "blocks"
    fake = Fake()  # the owner's part waits for an issue that exists (agents/planner.md "One issue or several")
    assert linear.run(["create", "ABC", "--title", "t", "--body-file", str(body), "--blocked-by", "ABC-12"], post=fake) == 0
    assert fake.calls[-1] == ("Relate", {"input": {"issueId": "uuid-12", "relatedIssueId": "uuid-40", "type": "blocks"}}), \
        "ABC-12 blocks the new issue"
    fake = Fake()
    assert linear.run(["create", "ABC", "--title", "t", "--body-file", str(body)], post=fake) == 0
    assert "assigneeId" not in fake.calls[-1][1]["input"] and "Relate" not in [name for name, _ in fake.calls]
    capsys.readouterr()


def test_create_files_nothing_when_a_user_or_a_key_is_wrong(tmp_path: Path, capsys):
    body = tmp_path / "h.md"
    body.write_text("From: ABC-12")
    for args, reason in ((["--assignee", "nobody"], "no user 'nobody'"), (["--assignee", "alex"], "'alex' names 2 users (Alex, Alex): give the email"),
                         (["--related", "ABC-999"], "no issue ABC-999"), (["--blocked-by", "ABC-999"], "no issue ABC-999")):
        fake = Fake()
        if args[0] in ("--related", "--blocked-by"):
            fake.issue = None
        assert linear.run(["create", "ABC", "--title", "t", "--body-file", str(body), *args], post=fake) == 1
        assert reason in capsys.readouterr().err
        assert "Create" not in [name for name, _ in fake.calls], "a typo files no issue"
    fake = Fake()
    assert linear.run(["create", "ABC", "--title", "t", "--body-file", str(body), "--assignee", "alex.b@example.com"], post=fake) == 0
    assert fake.calls[-1][1]["input"]["assigneeId"] == "u-b", "the email tells two users of one name apart"


def test_create_names_the_new_issue_when_a_link_fails(tmp_path: Path, capsys):
    """The issue exists once `issueCreate` succeeded, so a link that Linear refuses or does not answer fails
    with the new issue's key: a run that tries again links it, and never files a second issue."""
    body = tmp_path / "h.md"
    body.write_text("Found in ABC-12")

    def refused(query: str, variables: Any) -> dict[str, Any]:
        if query.startswith("mutation Relate"):
            fake.calls.append(("Relate", dict(variables)))
            return {"issueRelationCreate": {"success": False}}
        return fake(query, variables)

    def silent(query: str, variables: Any) -> dict[str, Any]:
        if query.startswith("mutation Relate"):
            raise linear.Failed("Linear did not answer: timed out")
        return fake(query, variables)

    for post, reason in ((refused, "issueRelationCreate refused for ABC-12"), (silent, "Linear did not answer: timed out")):
        fake = Fake()
        assert linear.run(["create", "ABC", "--title", "t", "--body-file", str(body), "--blocks", "ABC-12"], post=post) == 1
        err = capsys.readouterr().err
        assert "ABC-40 https://linear.app/x/ABC-40 was created, but these links were not: blocks ABC-12" in err
        assert reason in err and "do not create the issue again" in err
        assert [name for name, _ in fake.calls].count("Create") == 1
    fake = Fake()  # a failed link does not stop the others: each one is tried, and each missing one is named
    argv = ["create", "ABC", "--title", "t", "--body-file", str(body), "--related", "ABC-11", "--blocks", "ABC-12", "--blocked-by", "ABC-13"]
    assert linear.run(argv, post=refused) == 1
    err = capsys.readouterr().err
    assert "these links were not: related ABC-11 (" in err and "; blocks ABC-12 (" in err and "; blocked-by ABC-13 (" in err
    names = [name for name, _ in fake.calls]
    assert names.count("Relate") == 3 and names.count("Create") == 1


def by_key(fake: Fake) -> Any:
    """`fake`, with each issue read answered by its own key: `ABC-40` has the id `uuid-40`."""
    def post(query: str, variables: Any) -> dict[str, Any]:
        if query.startswith("query Issue("):
            fake.calls.append(("Issue", dict(variables)))
            key = str(variables["id"]).upper()
            return {"issue": None if key.endswith("-999") else {**ISSUE, "id": "uuid-" + key.split("-")[1], "identifier": key}}
        return fake(query, variables)
    return post


def test_link_links_an_issue_that_exists_in_each_kind(capsys):
    """A run whose base is on another issue's open pull request links its issue as blocked by that issue."""
    fake = Fake()
    fake.pages = [[listed("ABC-40", "In Progress")]]
    argv = ["link", "ABC-40", "--blocked-by", "abc-12", "--blocks", "ABC-13", "--related", "ABC-14"]
    assert linear.run(argv, post=by_key(fake)) == 0
    assert capsys.readouterr().out.splitlines() == ["related ABC-14: linked", "blocks ABC-13: linked", "blocked-by ABC-12: linked"]
    assert [v["input"] for n, v in fake.calls if n == "Relate"] == [
        {"issueId": "uuid-40", "relatedIssueId": "uuid-14", "type": "related"},
        {"issueId": "uuid-40", "relatedIssueId": "uuid-13", "type": "blocks"},
        {"issueId": "uuid-12", "relatedIssueId": "uuid-40", "type": "blocks"}], "ABC-12 blocks ABC-40"
    assert next(v for n, v in fake.calls if n == "Issues")["filter"] == {"team": {"key": {"eq": "ABC"}}, "number": {"in": [40]}}


def test_link_makes_no_link_that_the_issue_has_already(capsys):
    def other(key: str) -> dict[str, Any]:
        return {"identifier": key, "state": {"name": "In Review"}}

    fake = Fake()
    fake.pages = [[listed("ABC-40", "Todo", inverseRelations={"nodes": [{"type": "blocks", "issue": other("ABC-12")}]},
                          relations={"nodes": [{"type": "related", "relatedIssue": other("ABC-14")}]})]]
    assert linear.run(["link", "ABC-40", "--blocked-by", "ABC-12", "--related", "ABC-14", "--blocks", "ABC-12"], post=by_key(fake)) == 0
    assert capsys.readouterr().out.splitlines() == ["related ABC-14: linked already", "blocks ABC-12: linked", "blocked-by ABC-12: linked already"]
    assert [v["input"]["type"] for n, v in fake.calls if n == "Relate"] == ["blocks"], "a second run gives the same command"


def test_link_links_nothing_when_a_key_is_wrong_and_names_a_link_that_failed(capsys):
    for argv, reason in ((["link", "ABC-40", "--blocked-by", "ABC-12", "--related", "ABC-999"], "no issue ABC-999"),
                         (["link", "ABC-999", "--blocked-by", "ABC-12"], "no issue ABC-999"),
                         (["link", "ABC-40", "--blocked-by", "abc-40"], "ABC-40 cannot link to itself"),
                         (["link", "ABC-40"], "link needs --related, --blocks or --blocked-by")):
        fake = Fake()
        assert linear.run(argv, post=by_key(fake)) == 1
        assert reason in capsys.readouterr().err
        assert "Relate" not in [name for name, _ in fake.calls], "a typo links nothing"
    fake = Fake()
    fake.pages = [[listed("ABC-40", "Todo")]]
    post = by_key(fake)

    def refused(query: str, variables: Any) -> dict[str, Any]:
        if query.startswith("mutation Relate") and variables["input"]["type"] == "related":
            return {"issueRelationCreate": {"success": False}}
        return post(query, variables)

    assert linear.run(["link", "ABC-40", "--related", "ABC-14", "--blocked-by", "ABC-12"], post=refused) == 1
    assert "ABC-40: these links were not made: related ABC-14 (issueRelationCreate refused for ABC-14)" in capsys.readouterr().err
    assert [v["input"]["type"] for n, v in fake.calls if n == "Relate"] == ["blocks"], "the other link is still tried"


def test_escalated_lists_each_stay_with_its_exit(capsys):
    """The stays in Escalated, oldest first, whatever order the history comes in: `verdict.py answered` reads the owner's
    answers from them. A stay that has not ended is left out; one whose entry the history does not reach starts
    at ""."""
    history = [
        {"createdAt": "2026-10-23T18:00:00.000Z", "fromState": {"name": "Escalated"}, "toState": {"name": "In Progress"}},
        {"createdAt": "2026-10-20T10:00:00.000Z", "fromState": {"name": "Escalated"}, "toState": {"name": "Todo"}},
        {"createdAt": "2026-10-21T10:00:00.000Z", "fromState": {"name": "In Review"}, "toState": {"name": "Escalated"}},
        {"createdAt": "2026-10-22T10:00:00.000Z", "fromState": {"name": "Escalated"}, "toState": {"name": "Canceled"}},
        {"createdAt": "2026-10-23T10:00:00.000Z", "fromState": {"name": "Approved"}, "toState": {"name": "Escalated"}},
        {"createdAt": "2026-10-24T10:00:00.000Z", "fromState": None, "toState": None},
        {"createdAt": "2026-10-25T10:00:00.000Z", "fromState": {"name": "In Review"}, "toState": {"name": "Escalated"}},
    ]
    assert linear.run(["escalated", "ABC-12"], post=Fake(history=history)) == 0
    assert json.loads(capsys.readouterr().out) == [
        {"entered": "", "left": "2026-10-20T10:00:00.000Z", "to": "Todo"},
        {"entered": "2026-10-21T10:00:00.000Z", "left": "2026-10-22T10:00:00.000Z", "to": "Canceled"},
        {"entered": "2026-10-23T10:00:00.000Z", "left": "2026-10-23T18:00:00.000Z", "to": "In Progress"},
    ]
    assert linear.run(["escalated", "ABC-12"], post=Fake(history=[])) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_key_from_needs_no_api(capsys):
    assert linear.run(["key-from", "abc-12-x"]) == 0 and capsys.readouterr().out.strip() == "ABC-12"
    assert linear.run(["key-from", "plain"]) == 0 and capsys.readouterr().out.strip() == ""


def test_setup_makes_the_states_labels_automations_and_inbox_the_loop_needs(capsys):
    """agents/README.md "Setting up a product": one command makes the team, and reads before every change."""
    fake = Fake()
    assert linear.run(["setup", "abc"], post=fake) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[:6] == ["state Todo: exists", "state In Progress: exists", "state In Review: new (started)", "state Escalated: new (started)",
                       "state Approved: new (started)", "state Done: exists"]
    assert out[6:12] == ["label follow-up: exists (the workspace's)", "label fast-lane: new", "label hands-off: new", "label restart: new",
                         "label needs-judgment: new", "label intricate: new"], \
        "another team's label is not this team's; the difficulty labels are team labels"
    assert out[12:20] == ["label Area: new", *(f"label {a} (in Area): new"
                                               for a in ("backend", "frontend", "workflow", "infra", "orchestrator", "docs", "business"))]
    out = out[:10] + out[20:]
    assert out[10:15] == ["automation draft: none -> In Progress", "automation start: In Progress -> In Review", "automation review: In Progress -> none",
                         "automation mergeable: none (unchanged)", "automation merge: Done (unchanged)"]
    assert out[15] == "triage: off -> on" and out[16].startswith("team ABC now: states Todo, In Progress, Done, Canceled; triage off; no Triage state yet")
    made = [(name, v) for name, v in fake.calls if name == "StateCreate"]
    assert [v["input"]["name"] for _, v in made] == ["In Review", "Escalated", "Approved"]
    assert [v["input"]["position"] for _, v in made] == [3, 4, 5], "after In Progress, in the order the loop walks them"
    assert all(v["input"]["type"] == "started" and v["input"]["teamId"] == "team-uuid" and v["input"]["color"] for _, v in made)
    labels = [v["input"] for name, v in fake.calls if name == "LabelCreate"]
    assert [lb["name"] for lb in labels] == ["fast-lane", "hands-off", "restart", "needs-judgment", "intricate", "Area", "backend", "frontend",
                                               "workflow", "infra", "orchestrator", "docs", "business"]
    assert all("parentId" not in lb and "isGroup" not in lb and lb["color"] for lb in labels[3:5]), "no group, a colour each"
    assert labels[5]["isGroup"] is True and all(lb["parentId"] == "l-new-Area" and lb["description"] for lb in labels[6:]), \
        "the planner and /implement read one label of the group Area per issue (R1-3)"
    assert [v["id"] for name, v in fake.calls if name == "AutomationDelete"] == ["a-start", "a-review"], "the branch rule for main is not touched"
    rules = [v["input"] for name, v in fake.calls if name == "AutomationCreate"]
    assert rules == [{"teamId": "team-uuid", "event": "draft", "stateId": "s-progress"}, {"teamId": "team-uuid", "event": "start", "stateId": "s-new-In Review"}], \
        "no action is no rule: Linear refuses a rule for every branch with no state (2026-10-25)"
    assert [v["input"] for name, v in fake.calls if name == "TeamUpdate"] == [{"triageEnabled": True}]


def test_setup_changes_nothing_on_a_ready_team_and_on_a_dry_run(capsys):
    done = [{"id": "l-1", "name": "follow-up", "team": {"key": "ABC"}}, {"id": "l-2", "name": "hands-off", "team": {"key": "ABC"}}, {"id": "l-3", "name": "restart", "team": None},
            {"id": "l-5", "name": "fast-lane", "team": {"key": "ABC"}},
            {"id": "l-6", "name": "needs-judgment", "team": {"key": "ABC"}}, {"id": "l-7", "name": "intricate", "team": {"key": "ABC"}},
            {"id": "l-4", "name": "Area", "isGroup": True, "team": {"key": "ABC"}},
            *({"id": f"l-{a}", "name": a, "team": {"key": "ABC"}, "parent": {"name": "Area"}} for a in linear.AREAS)]
    fake = Fake(team=READY, labels=done)
    assert linear.run(["setup", "ABC"], post=fake) == 0
    out = capsys.readouterr().out
    assert "new" not in out and "->" not in out and "triage: on" in out and out.endswith("; triage on\n")
    assert [name for name, _ in fake.calls] == ["Setup", "Labels", "Setup"], "reads only"
    fake = Fake()
    assert linear.run(["setup", "ABC", "--dry-run"], post=fake) == 0
    out = capsys.readouterr().out
    assert "state In Review: new (started)" in out and out.endswith("dry run: nothing changed\n")
    assert [name for name, _ in fake.calls] == ["Setup", "Labels"], "a dry run sends no mutation"
    assert linear.run(["setup", "NOPE"], post=Fake(team=None)) == 1 and "no team NOPE" in capsys.readouterr().err


def test_setup_never_makes_a_second_label_of_a_name_outside_the_group(capsys):
    """Linear keeps one label name per team: a `docs` outside the group is reported, not made again; an area's label waits
    for its group, which a dry run does not make."""
    plain = [{"id": "l-docs", "name": "docs", "team": {"key": "ABC"}}]
    fake = Fake(team=READY, labels=plain)
    assert linear.run(["setup", "ABC"], post=fake) == 0
    out = capsys.readouterr().out
    assert "label docs: exists, but not as a label in Area: move it in Linear, then run setup again" in out
    assert "docs" not in [v["input"]["name"] for name, v in fake.calls if name == "LabelCreate"]
    fake = Fake(team=READY, labels=[])
    assert linear.run(["setup", "ABC", "--dry-run"], post=fake) == 0
    assert "label backend (in Area): new" in capsys.readouterr().out and "LabelCreate" not in [n for n, _ in fake.calls]


def test_setup_says_which_label_waits_and_where_it_belongs(capsys):
    """With `Area` a plain label, the area labels wait for the group (R1-3); a label of LABELS inside a group
    belongs outside any group (R1-4)."""
    taken = [{"id": "l-area", "name": "Area", "team": {"key": "ABC"}},
             {"id": "l-fu", "name": "follow-up", "team": {"key": "ABC"}, "parent": {"name": "Kind"}}]
    for dry in ([], ["--dry-run"]):
        fake = Fake(team=READY, labels=taken)
        assert linear.run(["setup", "ABC", *dry], post=fake) == 0
        out = capsys.readouterr().out.splitlines()
        assert "label Area: exists, but not as the group: move it in Linear, then run setup again" in out
        assert "label follow-up: exists, but not as a label outside any group: move it in Linear, then run setup again" in out
        assert [line for line in out if "(in Area)" in line] == [f"label {a} (in Area): waits for the group" for a in linear.AREAS]
        made = [v["input"]["name"] for name, v in fake.calls if name == "LabelCreate"]
        assert made == ([] if dry else ["fast-lane", "hands-off", "restart", "needs-judgment", "intricate"])


def test_the_trigger_goes_right_after_the_origin_line():
    assert linear.with_trigger("Found in ABC-1\n\nWhy: a flake.\n", "the test is flaky again") == (
        "Found in ABC-1\nTake it when: the test is flaky again\n\nWhy: a flake.\n")
    assert linear.with_trigger("From: PR #5, round 2\nWhy", "x").splitlines()[:2] == ["From: PR #5, round 2", "Take it when: x"]
    assert linear.with_trigger("Why: no origin.", "x") == "Take it when: x\nWhy: no origin."
    assert linear.with_trigger("", "x") == "Take it when: x"


def test_the_trigger_goes_after_a_collecting_follow_ups_block_of_origins():
    """A trigger inside the block of `From: PR` lines hid the later origins from the orchestrator's wait
    (`origin_prs` stops at the first other line) and from the reviewer's depth."""
    two = "From: PR #57, round 2, abc1234, depth 1\nFrom: PR #60, round 1, def5678, depth 2\n\n## From PR #57\n\n- [ ] a\n"
    assert linear.with_trigger(two, "x").splitlines()[:3] == [
        "From: PR #57, round 2, abc1234, depth 1", "From: PR #60, round 1, def5678, depth 2", "Take it when: x"]
    assert linear.with_trigger("From: PR #5, round 2\nWhy", "x").splitlines()[:2] == ["From: PR #5, round 2", "Take it when: x"]
    assert linear.with_trigger("Found in ABC-1\nFrom: PR #9, round 1\n", "x") == "Found in ABC-1\nTake it when: x\nFrom: PR #9, round 1\n", \
        "a `Found in` origin keeps its one line"
    # a review that joins the parked issue later adds its line to the block, before the trigger
    parked = linear.with_trigger(two, "x")
    assert linear.with_origin(parked, "PR #70, round 1, 0a0a0a0, depth 1", "- [ ] b").splitlines()[:4] == [
        "From: PR #57, round 2, abc1234, depth 1", "From: PR #60, round 1, def5678, depth 2",
        "From: PR #70, round 1, 0a0a0a0, depth 1", "Take it when: x"]


def test_the_trigger_replaces_the_one_the_text_held():
    text = "Found in ABC-1\nTake it when: when friction shows\nWhy\n  Take it when: a second one"
    assert linear.with_trigger(text, "  the hourly run\n takes  2 minutes ") == (
        "Found in ABC-1\nTake it when: the hourly run takes 2 minutes\nWhy")
    assert linear.with_trigger("**Take it when:** old.\n\nFrom: ROADMAP.md\n\n- [ ] x", "new") == (
        "From: ROADMAP.md\nTake it when: new\n\n- [ ] x"), "the bold trigger of older issues goes, and the origin line is first"


def test_trigger_writes_the_text_and_refuses_an_empty_one(capsys):
    issue = {**ISSUE, "description": "Found in ABC-9\nThe flake."}
    fake = Fake(issue=issue)
    assert linear.run(["trigger", "ABC-12", "--when", "the test is flaky again on CI (its issue gets a comment)"], post=fake) == 0
    assert capsys.readouterr().out.strip() == "set"
    assert fake.calls[-1] == ("Describe", {"id": "uuid-12", "text": "Found in ABC-9\nTake it when: the test is flaky again on CI (its issue gets a comment)\nThe flake."})
    fake = Fake(issue=issue)
    assert linear.run(["trigger", "ABC-12", "--when", "  "], post=fake) == 1
    assert "the trigger is empty" in capsys.readouterr().err
    assert [c[0] for c in fake.calls] == ["Issue"], "nothing written"


def test_unlabel_removes_the_label_and_fails_when_it_is_not_there(capsys):
    """A run that acts on `restart` removes the label first: that is its claim, and a second run then finds none (R2-1)."""
    fake = Fake(issue={**ISSUE, "labels": {"nodes": [{"name": "restart"}]}}, labels=[{"id": "l-r", "name": "restart", "team": None}])
    assert linear.run(["unlabel", "ABC-12", "restart"], post=fake) == 0 and capsys.readouterr().out == "removed\n"
    assert [v for name, v in fake.calls if name == "Unlabel"] == [{"id": "uuid-12", "label": "l-r"}]
    fake = Fake()
    assert linear.run(["unlabel", "ABC-12", "restart"], post=fake) == 1
    assert "ABC-12 has no label 'restart'" in capsys.readouterr().err and "Unlabel" not in [n for n, _ in fake.calls]


def test_label_adds_the_label_passes_when_it_is_there_and_fails_for_an_unknown_one(capsys):
    """A run that finds its issue harder than its difficulty labels adds the missing one (agents/implementer.md)."""
    known = [{"id": "l-i", "name": "intricate", "team": {"key": "ABC"}}]
    fake = Fake(labels=known)
    assert linear.run(["label", "ABC-12", "intricate"], post=fake) == 0 and capsys.readouterr().out == "added\n"
    assert [v for name, v in fake.calls if name == "Label"] == [{"id": "uuid-12", "label": "l-i"}]
    fake = Fake(issue={**ISSUE, "labels": {"nodes": [{"name": "intricate"}]}}, labels=known)
    assert linear.run(["label", "ABC-12", "intricate"], post=fake) == 0 and capsys.readouterr().out == "added already\n"
    assert "Label" not in [n for n, _ in fake.calls], "nothing sent"
    fake = Fake(labels=[{"id": "l-x", "name": "intricate", "team": {"key": "OTHER"}}])
    assert linear.run(["label", "ABC-12", "intricate"], post=fake) == 1
    assert "no label 'intricate' for team ABC" in capsys.readouterr().err and "Label" not in [n for n, _ in fake.calls]


def test_every_connection_in_a_query_names_its_page_size():
    """Linear refused the setup query as too complex (12315 of 10000, 2026-10-25): an unsized connection counts as 50
    rows and nested ones multiply, so every connection says how many rows it wants."""
    import re

    source = Path(linear.__file__).read_text()
    connections = re.findall(r"(\w+)(\([^)]*\))?\s*\{\s*nodes", source)
    assert connections and len(connections) == len(re.findall(r"\{\s*nodes\b", source)), "each `{ nodes` is a connection the pattern reads"
    for name, args in connections:
        assert "first:" in args or "last:" in args, f"{name} has no first: (or last:, the GitHub query of open_pulls)"


def test_open_pulls_reads_the_teams_repository_or_the_current_folders():
    """A team per repository (AGENTS.md "The teams"). `triage-checks XYZ` run from the product checkout
    must read the other repository's pull requests, so `--repo` names the repository; with none, gh fills in the current one."""
    import subprocess

    seen: list[list[str]] = []
    node = {"number": 7, "headRefName": "xyz-2-x", "isDraft": False, "commits": {"nodes": [{"commit": {"committedDate": "d"}}]}}

    def run(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        seen.append(command)
        return subprocess.CompletedProcess(command, 0, json.dumps(node) + "\n", "")

    pulls = linear.open_pulls("acme/tools", run=run)
    assert pulls == [{"number": 7, "headRefName": "xyz-2-x", "isDraft": False, "commits": [{"committedDate": "d"}]}]
    assert "owner=acme" in seen[0] and "repo=tools" in seen[0]
    linear.open_pulls(run=run)
    assert "owner={owner}" in seen[1] and "repo={repo}" in seen[1], "gh's own placeholders: the current folder's repository"


def test_a_failed_connection_says_why_and_never_the_key(monkeypatch: pytest.MonkeyPatch):
    """The owner's first run printed "Linear did not answer: URLError" (2026-10-25): a Python with no root certificates,
    which the message now says. The reason is the cause, never the request, which carries the key."""
    import ssl
    import urllib.error

    def refuse(request, timeout):
        raise urllib.error.URLError(ssl.SSLCertVerificationError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed"))

    monkeypatch.setattr(linear.urllib.request, "urlopen", refuse)
    post = linear.http_post("lin_api_never_printed")
    with pytest.raises(linear.Failed) as failed:
        post("query X { x }", {})
    said = str(failed.value)
    assert "CERTIFICATE_VERIFY_FAILED" in said and "no root certificates" in said and "lin_api" not in said


def listed(key: str, state: str, **fields: Any) -> dict[str, Any]:
    """An issue as the Issues query returns it."""
    return {"identifier": key, "title": f"Title of {key}", "description": None, "url": None, "priority": 0, "estimate": None,
            "createdAt": "2026-10-20T00:00:00Z", "updatedAt": "2026-10-26T00:00:00Z", "state": {"name": state}, "assignee": None,
            "project": None, "labels": {"nodes": []}, "relations": {"nodes": []}, "inverseRelations": {"nodes": []},
            "comments": {"nodes": []}, **fields}


def test_list_reads_every_page_with_what_the_triage_checks(capsys):
    """The triage (agents/planner.md) reads the team's issues with their labels, priority, size, relations and
    comments: a blocker's state shows whether it is still open, and every page is read."""
    def link(key: str, state: str) -> dict[str, Any]:
        return {"identifier": key, "state": {"name": state}}

    fake = Fake()
    fake.pages = [[listed("ABC-5", "Triage", priority=3, estimate=2, prioritySortOrder=-523785.5, labels={"nodes": [{"name": "workflow", "parent": {"name": "Area"}}, {"name": "Bug", "parent": None}]},
                          relations={"nodes": [{"type": "blocks", "relatedIssue": link("ABC-9", "Backlog")},
                                               {"type": "duplicate", "relatedIssue": link("ABC-2", "Todo")}]},
                          inverseRelations={"nodes": [{"type": "blocks", "issue": link("ABC-1", "Done")},
                                                      {"type": "related", "issue": link("ABC-3", "Todo")}]},
                          comments={"nodes": [{"createdAt": "2026-10-22T00:00:00Z", "body": "second", "user": None},
                                              {"createdAt": "2026-10-21T00:00:00Z", "body": "first", "user": {"name": "Mauro"}}]})],
                  [listed("ABC-9", "Backlog")]]
    assert linear.run(["list", "abc"], post=fake) == 0
    first, second = json.loads(capsys.readouterr().out)
    assert first["labels"] == ["workflow", "Bug"] and first["areas"] == ["workflow"], "the Area is the label of the group Area"
    assert (first["priority"], first["size"]) == ("medium", "S")
    assert first["manual_order"] == -523785.5 and second["manual_order"] is None, "the owner's order in Linear"
    assert first["blocked_by"] == [{"key": "ABC-1", "state": "Done"}] and first["blocks"] == [{"key": "ABC-9", "state": "Backlog"}]
    assert first["related"] == [{"key": "ABC-3", "state": "Todo"}] and first["duplicate_of"] == "ABC-2"
    assert [c["body"] for c in first["comments"]] == ["first", "second"] and first["comments"][0]["author"] == "Mauro"
    assert (second["identifier"], second["priority"], second["size"], second["description"]) == ("ABC-9", None, None, "")
    queries = [v for n, v in fake.calls if n == "Issues"]
    assert [q["after"] for q in queries] == [None, "1"]
    assert queries[0]["filter"] == {"team": {"key": {"eq": "ABC"}}, "state": {"type": {"nin": ["completed", "canceled"]}}}, \
        "every open issue by default"


def test_list_reads_every_comment_label_and_relation_past_the_first_page(capsys):
    """The Issues query reads 30 comments, 10 labels and 20 relations of each issue: an issue with more showed the triage
    only part of them, and the owner's answer can be the comment left out. The pages after the first come one
    request each, only for a connection that has more."""
    def comment(day: int, body: str) -> dict[str, Any]:
        return {"createdAt": f"2026-10-{day:02d}T00:00:00Z", "body": body, "user": {"name": "Mauro"}}

    fake = Fake()
    fake.pages = [[listed("ABC-5", "Triage", comments={"nodes": [comment(1, "c1"), comment(2, "c2")], "pageInfo": {"hasNextPage": True, "endCursor": "1"}},
                          labels={"nodes": [{"name": "workflow"}], "pageInfo": {"hasNextPage": True, "endCursor": "1"}},
                          inverseRelations={"nodes": [], "pageInfo": {"hasNextPage": True, "endCursor": "1"}}),
                   listed("ABC-6", "Triage", comments={"nodes": [comment(1, "only")], "pageInfo": {"hasNextPage": False, "endCursor": "1"}})]]
    fake.more = {("ABC-5", "comments"): [[comment(4, "c4"), comment(3, "c3")], [comment(5, "the owner's answer")]],
                 ("ABC-5", "labels"): [[{"name": "follow-up"}, {"name": "frontend", "parent": {"name": "Area"}}]],
                 ("ABC-5", "inverseRelations"): [[{"type": "blocks", "issue": {"identifier": "ABC-1", "state": {"name": "Todo"}}}]]}
    assert linear.run(["list", "ABC"], post=fake) == 0
    first, second = json.loads(capsys.readouterr().out)
    assert [c["body"] for c in first["comments"]] == ["c1", "c2", "c3", "c4", "the owner's answer"]
    assert first["labels"] == ["workflow", "follow-up", "frontend"] and first["areas"] == ["frontend"] and first["blocked_by"] == [{"key": "ABC-1", "state": "Todo"}]
    assert [c["body"] for c in second["comments"]] == ["only"]
    more = [(v["id"], v["after"]) for n, v in fake.calls if n == "More"]
    assert sorted(more) == [("ABC-5", "1"), ("ABC-5", "1"), ("ABC-5", "1"), ("ABC-5", "2")], "a request per page after the first, ABC-6 none"


def test_list_takes_the_states_named_and_since_reads_when_each_entered_it(capsys):
    fake = Fake(history=[{"createdAt": "2026-10-21T00:00:00Z", "toState": {"name": "Escalated"}},
                         {"createdAt": "2026-10-23T00:00:00Z", "toState": {"name": "Escalated"}},
                         {"createdAt": "2026-10-25T00:00:00Z", "toState": {"name": "Todo"}}])
    fake.pages = [[listed("ABC-7", "Escalated")]]
    assert linear.run(["list", "ABC", "--state", "Escalated", "--state", "Triage", "--since"], post=fake) == 0
    [issue] = json.loads(capsys.readouterr().out)
    assert issue["since"] == "2026-10-23T00:00:00Z", "the last move into the state it is in"
    assert next(v for n, v in fake.calls if n == "Issues")["filter"]["state"] == {"name": {"in": ["Escalated", "Triage"]}}
    fake = Fake()
    fake.pages = [[listed("ABC-8", "Triage")]]
    assert linear.run(["list", "ABC", "--since"], post=fake) == 0
    assert json.loads(capsys.readouterr().out)[0]["since"] == "2026-10-20T00:00:00Z", "made in its state: its creation"


def test_list_updated_since_sends_the_date_filter(capsys):
    """The filter goes to Linear, so the triage reads the last 30 days of closed issues and not every one."""
    fake = Fake()
    fake.pages = [[listed("ABC-9", "Done")]]
    assert linear.run(["list", "ABC", "--state", "Done", "--updated-since", "30"], post=fake) == 0
    capsys.readouterr()
    scope = next(v for n, v in fake.calls if n == "Issues")["filter"]
    cut = datetime.fromisoformat(scope["updatedAt"]["gt"])
    assert abs((datetime.now(UTC) - cut) - timedelta(days=30)) < timedelta(minutes=5)
    fake = Fake()
    fake.pages = [[listed("ABC-9", "Done")]]
    assert linear.run(["list", "ABC", "--state", "Done"], post=fake) == 0
    assert "updatedAt" not in next(v for n, v in fake.calls if n == "Issues")["filter"], "no flag: no date cut"


def test_triage_set_changes_only_an_issue_in_triage(capsys):
    """The issue's author and the triage set the priority and the size in Triage; after it they are the owner's
    (agents/README.md "The issue states")."""
    fake = Fake(issue={**ISSUE, "state": {"name": "Triage", "type": "triage"}})
    assert linear.run(["triage-set", "ABC-12", "--priority", "low", "--size", "XS"], post=fake) == 0
    assert fake.calls[-1] == ("Set", {"id": "uuid-12", "input": {"priority": 4, "estimate": 1}})
    fake = Fake()
    assert linear.run(["triage-set", "ABC-12", "--priority", "high"], post=fake) == 1
    assert "not Triage" in capsys.readouterr().err and "Set" not in [n for n, _ in fake.calls]
    assert linear.run(["triage-set", "ABC-12"], post=Fake(issue={**ISSUE, "state": {"name": "Triage", "type": "triage"}})) == 1
    assert "nothing to set" in capsys.readouterr().err


def test_list_by_key_reads_the_issues_named_in_any_state(capsys):
    """A skill reads one issue with its text, comments and relations, whatever its state (the skills name the
    action, and `issue KEY` reads no comment)."""
    fake = Fake()
    fake.pages = [[listed("ABC-12", "Done", comments={"nodes": [{"createdAt": "2026-10-21T00:00:00Z", "body": "an answer", "user": None}]})]]
    assert linear.run(["list", "ABC", "--key", "abc-12", "--key", "ABC-7"], post=fake) == 0
    [issue] = json.loads(capsys.readouterr().out)
    assert issue["identifier"] == "ABC-12" and [c["body"] for c in issue["comments"]] == ["an answer"]
    query = next(v for n, v in fake.calls if n == "Issues")
    assert query["filter"] == {"team": {"key": {"eq": "ABC"}}, "number": {"in": [12, 7]}}, "no state filter: a Done issue is read too"
    fake = Fake()
    assert linear.run(["list", "ABC", "--key", "ABC-12", "--state", "Todo"], post=fake) == 0
    capsys.readouterr()
    assert next(v for n, v in fake.calls if n == "Issues")["filter"]["state"] == {"name": {"in": ["Todo"]}}
    fake = Fake()
    assert linear.run(["list", "ABC", "--key", "OTHER-3"], post=fake) == 1
    assert "not an issue key of team ABC" in capsys.readouterr().err and not fake.calls


def test_create_puts_the_issue_in_the_project_named(tmp_path: Path, capsys):
    """The planner files a feature's issues in its project (agents/planner.md "The issue"); a wrong name files nothing."""
    body = tmp_path / "f.md"
    body.write_text("From: the owner's idea")
    fake = Fake()
    assert linear.run(["create", "ABC", "--title", "t", "--body-file", str(body), "--project", "agent WORKFLOW"], post=fake) == 0
    capsys.readouterr()
    assert fake.calls[-1][1]["input"]["projectId"] == "p-workflow"
    fake = Fake()
    assert linear.run(["create", "ABC", "--title", "t", "--body-file", str(body), "--project", "Nope"], post=fake) == 1
    assert "no project 'Nope'" in capsys.readouterr().err and "Create" not in [n for n, _ in fake.calls]


def test_project_makes_a_project_once(tmp_path: Path, capsys):
    """A feature of several pull requests is a project whose content is the specification (agents/planner.md); a name
    that is taken is refused, so a second copy is never made."""
    body = tmp_path / "spec.md"
    body.write_text("The problem, who it is for, what done looks like.")
    fake = Fake()
    assert linear.run(["project", "abc", "--name", "Weekly email", "--body-file", str(body)], post=fake) == 0
    assert capsys.readouterr().out.strip() == "Weekly email https://linear.app/x/project/new"
    assert fake.calls[-1] == ("ProjectCreate", {"input": {"name": "Weekly email", "teamIds": ["team-uuid"], "content": body.read_text()}})
    fake = Fake()
    assert linear.run(["project", "ABC", "--name", "Agent workflow", "--body-file", str(body)], post=fake) == 1
    assert "exists already" in capsys.readouterr().err and "ProjectCreate" not in [n for n, _ in fake.calls]


FU_LABELS = [{"id": "l-fu", "name": "follow-up", "team": None}, {"id": "l-fl", "name": "fast-lane", "team": {"key": "ABC"}},
             {"id": "l-fe", "name": "frontend", "team": {"key": "ABC"}, "parent": {"name": "Area"}}]
FIRST = "PR #57, round 2, abc1234, depth 1"


def collecting(key: str, state: str, lines: int, *labels: str, priority: int = 4) -> dict[str, Any]:
    """A collecting follow-up as the Issues query returns it: `lines` findings from PR #57."""
    text = f"From: {FIRST}\n\n## From PR #57\n\n" + "".join(f"- [ ] R1-{n} a.py:{n}: one\n" for n in range(1, lines + 1))
    return listed(key, state, description=text, priority=priority, labels={"nodes": [{"name": n} for n in labels]})


def follow_up(tmp_path: Path, fake: Fake, *more: str, lines: int = 1, origin: str = "PR #60, round 1, def5678, depth 2") -> tuple[int, dict[str, Any] | None]:
    """Run the command with `lines` findings; the input of its write (Append or Create), or None."""
    body = tmp_path / "f.md"
    body.write_text("".join(f"- [ ] R1-{n} web/src/x.svelte:{n}: a check is missing\n" for n in range(1, lines + 1)))
    code = linear.run(["follow-up", "ABC", "--area", "frontend", "--from", origin, "--body-file", str(body),
                       "--priority", "medium", *more], post=fake)
    writes = [(n, v) for n, v in fake.calls if n in ("Append", "Create")]
    return code, (writes[-1][1] | {"op": writes[-1][0]}) if writes else None


def fake_with(*issues: dict[str, Any], now: str | None = None) -> Fake:
    """A team whose Triage and Todo hold `issues`; `Issue` reads the first of them back in the state `now`."""
    fake = Fake(labels=FU_LABELS)
    fake.pages = [list(issues)]
    if issues:
        first = issues[0]
        fake.issue = {**ISSUE, "id": "uuid-" + first["identifier"], "identifier": first["identifier"],
                      "description": first["description"], "state": {"name": now or first["state"]["name"], "type": "x"}}
    return fake


def test_follow_up_creates_the_areas_collecting_issue_when_none_is_open(tmp_path: Path, capsys):
    """The first review of an Area files the collecting issue: its origin line first, the findings under
    `## From PR #N`, size S, in Triage, or in Todo with `fast-lane` when the findings pass the fast lane's test."""
    fake = fake_with()
    code, made = follow_up(tmp_path, fake)
    assert code == 0 and made is not None and made["op"] == "Create"
    assert capsys.readouterr().out.strip() == "ABC-40 created https://linear.app/x/ABC-40"
    issue = made["input"]
    assert issue["title"] == "Follow-ups: frontend, from PR #60"
    assert issue["description"].startswith("From: PR #60, round 1, def5678, depth 2\n\n## From PR #60\n\n- [ ] R1-1 ")
    assert (issue["stateId"], sorted(issue["labelIds"]), issue["priority"], issue["estimate"]) == \
        ("s-triage", ["l-fe", "l-fu"], 3, 2)
    code, made = follow_up(tmp_path, fake_with(), "--fast-lane")
    assert made is not None and made["input"]["stateId"] == "s-todo" and "l-fl" in made["input"]["labelIds"]
    capsys.readouterr()


def test_follow_up_appends_a_second_reviews_findings_to_the_open_collecting_issue(tmp_path: Path, capsys):
    """Two reviews of the same Area that approve while the first follow-up issue still waits give one issue with both
    origins: the second `From:` line right under the first, its findings at the end, the priority raised to the higher."""
    fake = fake_with(collecting("ABC-50", "Triage", 2, "follow-up", "frontend"))
    code, made = follow_up(tmp_path, fake)
    assert code == 0 and made is not None and made["op"] == "Append" and made["id"] == "uuid-ABC-50"
    assert capsys.readouterr().out.strip() == "ABC-50 appended https://linear.app/x/ABC-12"
    text = made["input"]["description"]
    assert text.splitlines()[:2] == [f"From: {FIRST}", "From: PR #60, round 1, def5678, depth 2"], "both origins at the top"
    assert text.index("## From PR #57") < text.index("## From PR #60") and linear.findings(text) == 3
    assert made["input"]["priority"] == 3, "low raised to medium"
    _, made = follow_up(tmp_path, fake_with(collecting("ABC-50", "Triage", 2, "follow-up", "frontend", priority=2)))
    assert made is not None and "priority" not in made["input"], "a higher priority stays"
    capsys.readouterr()


def test_follow_up_takes_six_findings_and_the_seventh_opens_a_new_issue(tmp_path: Path, capsys):
    _, made = follow_up(tmp_path, fake_with(collecting("ABC-50", "Triage", 5, "follow-up", "frontend")))
    assert made is not None and made["op"] == "Append", "the sixth finding"
    _, made = follow_up(tmp_path, fake_with(collecting("ABC-50", "Triage", 6, "follow-up", "frontend")))
    assert made is not None and made["op"] == "Create", "the seventh opens a new issue"
    _, made = follow_up(tmp_path, fake_with(collecting("ABC-50", "Triage", 4, "follow-up", "frontend")), lines=3)
    assert made is not None and made["op"] == "Create", "a review's findings stay together: 4 + 3 is past six"
    capsys.readouterr()


def test_follow_up_never_appends_to_a_started_issue_another_area_or_the_other_lane(tmp_path: Path, capsys):
    """An issue that a run started takes no more findings (read again right before the write); a finding never enters the
    fast lane by another finding's test, nor an issue that the owner moved out of Triage."""
    _, made = follow_up(tmp_path, fake_with(collecting("ABC-50", "Triage", 1, "follow-up", "frontend"), now="In Progress"))
    assert made is not None and made["op"] == "Create", "started since the list was read"
    _, made = follow_up(tmp_path, fake_with(collecting("ABC-50", "Triage", 1, "follow-up", "backend")))
    assert made is not None and made["op"] == "Create", "another Area"
    _, made = follow_up(tmp_path, fake_with(collecting("ABC-50", "Triage", 1, "frontend")))
    assert made is not None and made["op"] == "Create", "no follow-up label: not a collecting issue"
    _, made = follow_up(tmp_path, fake_with(collecting("ABC-50", "Triage", 1, "follow-up", "frontend")), "--fast-lane")
    assert made is not None and made["op"] == "Create", "fast-lane findings never join a Triage issue"
    _, made = follow_up(tmp_path, fake_with(collecting("ABC-50", "Todo", 1, "follow-up", "frontend", "fast-lane")))
    assert made is not None and made["op"] == "Create", "other findings never join a fast-lane issue"
    _, made = follow_up(tmp_path, fake_with(collecting("ABC-50", "Todo", 1, "follow-up", "frontend", "fast-lane")), "--fast-lane")
    assert made is not None and made["op"] == "Append"
    # The orchestrator starts the run with its run line, and the run claims the issue only after reading
    # it: an issue with a run line is started, though still in Todo
    started = collecting("ABC-50", "Todo", 1, "follow-up", "frontend", "fast-lane")
    started["comments"] = {"nodes": [{"createdAt": "2026-11-16T10:00:00Z", "body": "Run: m, effort high", "user": None}]}
    _, made = follow_up(tmp_path, fake_with(started), "--fast-lane")
    assert made is not None and made["op"] == "Create", "its run started: a new issue takes the findings"
    capsys.readouterr()


@pytest.fixture(autouse=True)
def no_settle(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """The pauses before a write is read back, kept instead of slept (every test of this file: an append pauses)."""
    slept: list[float] = []
    monkeypatch.setattr(linear.time, "sleep", slept.append)
    return slept


OTHER = "PR #61, round 2, aaa1111, depth 1"


def with_other(text: str) -> str:
    """The text that another review of the Area wrote: from what it read before our write, so without our section."""
    before = "\n".join(line for line in text.splitlines() if not line.startswith("From: PR #60"))
    return linear.with_origin(before.split("\n\n## From PR #60")[0], OTHER, "- [ ] R2-1 web/src/y.svelte:1: another finding")


def test_follow_up_reads_the_count_again_before_its_write(tmp_path: Path, capsys, no_settle):
    """The six-finding limit holds against the text read right before the write, not the list."""
    fake = fake_with(collecting("ABC-50", "Triage", 2, "follow-up", "frontend"))
    assert fake.issue is not None
    fake.issue = {**fake.issue, "description": collecting("ABC-50", "Triage", 6, "follow-up", "frontend")["description"]}
    _, made = follow_up(tmp_path, fake)
    assert made is not None and made["op"] == "Create", "another review filled it since the list was read"
    capsys.readouterr()


def drop_first(text: str) -> str:
    """The text that a write from an older read leaves: without the first review's origin line and section."""
    lines = [line for line in text.splitlines() if line != f"From: {FIRST}"]
    rest = "\n".join(lines)
    head, cut, tail = rest.partition("## From PR #57\n\n- [ ] R1-1 a.py:1: one\n- [ ] R1-2 a.py:2: one")
    assert cut, "the first review's section is in the text"
    return head.rstrip() + tail


def test_follow_up_writes_again_when_another_reviews_write_removed_its_section(tmp_path: Path, capsys, no_settle):
    """Two reviews of one Area append at the same moment, and the later write drops the earlier section. The
    write is read back after a pause and written again with every section that it wanted, the other's kept: its own
    (the first write), and one that the text held before (the second write)."""
    fake = fake_with(collecting("ABC-50", "Triage", 2, "follow-up", "frontend"))
    fake.on_append = [with_other, drop_first]
    code, _ = follow_up(tmp_path, fake)
    assert code == 0 and capsys.readouterr().out.strip() == "ABC-50 appended https://linear.app/x/ABC-12"
    assert [n for n, _ in fake.calls].count("Append") == 3 and no_settle == [linear.SETTLE_SECONDS] * 3
    assert fake.issue is not None
    text = fake.issue["description"]
    assert sorted(text.splitlines()[:3]) == sorted([f"From: {FIRST}", f"From: {OTHER}", "From: PR #60, round 1, def5678, depth 2"])
    for section in ("## From PR #57", "## From PR #61", "## From PR #60"):
        assert text.count(f"{section}\n") == 1, section
    assert linear.findings(text) == 4, "each finding once"


def test_follow_up_files_a_new_issue_when_each_write_was_removed(tmp_path: Path, capsys, no_settle):
    fake = fake_with(collecting("ABC-50", "Triage", 2, "follow-up", "frontend"))
    fake.on_append = [with_other] * linear.WRITES
    _, made = follow_up(tmp_path, fake)
    assert made is not None and made["op"] == "Create" and [n for n, _ in fake.calls].count("Append") == linear.WRITES
    capsys.readouterr()


def test_entries_pair_each_origin_with_its_section_and_restored_adds_only_what_is_missing():
    text = linear.with_trigger(collecting("ABC-50", "Triage", 1, "follow-up")["description"], "the owner says so")
    mine = ("From: PR #60, round 1, def5678, depth 2", "## From PR #60\n\n- [ ] R1-1 b.py:1: two")
    assert linear.entries(text) == [(f"From: {FIRST}", "## From PR #57\n\n- [ ] R1-1 a.py:1: one")]
    both = linear.restored(text, [*linear.entries(text), mine])
    assert both.splitlines()[:3] == [f"From: {FIRST}", mine[0], "Take it when: the owner says so"]
    assert linear.entries(both)[1] == mine and linear.restored(both, [mine]) == both


def test_follow_up_refuses_an_origin_or_a_body_it_cannot_read(tmp_path: Path, capsys):
    code, made = follow_up(tmp_path, fake_with(), origin="PR #60, round 1")
    assert code == 1 and made is None and "is not `PR #N, round R, HEAD, depth D`" in capsys.readouterr().err
    code, made = follow_up(tmp_path, fake_with(), lines=0)
    assert code == 1 and made is None and "no finding" in capsys.readouterr().err



def test_the_areas_come_from_linear_areas_file(tmp_path: Path):
    """Another product names its own Area labels and texts in a JSON file; with no file, the areas are DEFAULT_AREAS. A file
    that is no object of texts stops the script before it makes a wrong label."""
    assert linear.read_areas({}) == linear.DEFAULT_AREAS
    assert linear.AREAS == linear.DEFAULT_AREAS, "the tests run with no LINEAR_AREAS_FILE"
    areas = tmp_path / "areas.json"
    areas.write_text(json.dumps({"api": "server/: the API.", "app": "client/: the pages."}))
    assert linear.read_areas({"LINEAR_AREAS_FILE": str(areas)}) == {"api": "server/: the API.", "app": "client/: the pages."}
    for bad in ("[]", "{}", '{"api": 1}', "not json"):
        areas.write_text(bad)
        with pytest.raises(SystemExit, match="LINEAR_AREAS_FILE"):
            linear.read_areas({"LINEAR_AREAS_FILE": str(areas)})
    with pytest.raises(SystemExit, match="LINEAR_AREAS_FILE"):
        linear.read_areas({"LINEAR_AREAS_FILE": str(tmp_path / "none.json")})
