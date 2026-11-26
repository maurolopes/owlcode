"""Each flaky browser check on CI files or updates its own Linear issue (.github/flakes.py, .github/workflows/flakes.yml).
Linear is a fake client, never the network."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("github_flakes", REPO / ".github" / "flakes.py")
assert spec and spec.loader
flakes = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = flakes
spec.loader.exec_module(flakes)

RUN = "https://github.com/owner/repo/actions/runs/1"
HEAD = "a" * 40


def flake(location: str = "alpha.spec.mjs:95", name: str = "alpha › first screen") -> dict[str, Any]:
    file, line = location.split(":")
    return {"name": name, "file": f"tests/pw/{file}", "line": int(line), "location": location,
            "error": "Error: expect(locator).toBeVisible() failed", "trace": ".screenshots/pw-results/x/trace.zip"}


def issue(key: str, title: str, description: str = "", priority: str = "high", created: str = "2026-11-05") -> dict[str, Any]:
    return {"identifier": key, "title": title, "description": description, "priority": priority, "created": created}


def plan(found: list[dict[str, Any]], issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return flakes.plan(found, issues, RUN, HEAD, "Found in ABC-12")


def test_a_new_flake_gets_its_issue_in_the_fast_lane():
    [action] = plan([flake()], [issue("ABC-1", "an unrelated issue")])
    assert action["action"] == "create"
    assert action["title"] == 'alpha.spec.mjs:95 "alpha › first screen" is flaky on CI'
    body = action["body"]
    assert body.startswith("Found in ABC-12\n"), "the origin line first (agents/planner.md)"
    assert RUN in body and HEAD[:12] in body and "toBeVisible() failed" in body and "web/.screenshots/pw-results/x/trace.zip" in body


def test_a_second_flake_of_a_test_comments_on_its_open_issue_and_makes_it_urgent():
    """The open issue names the test's file:line (an existing issue's title does), or else its name; Backlog counts as open."""
    issues = [issue("ABC-476", "alpha.spec.mjs:95 never finds the card"), issue("ABC-2", "other")]
    [action] = plan([flake()], issues)
    assert action == {**action, "action": "comment", "key": "ABC-476", "urgent": False}
    assert "Urgent now" in action["body"] and RUN in action["body"] and "toBeVisible" in action["body"]
    assert action["body"].startswith("Found in ABC-12: flaky again"), "where it was found, first"
    [on_main] = flakes.plan([flake()], issues, RUN, HEAD, flakes.origin("main", None))
    assert on_main["body"].startswith("Found on main: flaky again"), "a flake of main's green run lands with its origin"
    [by_name] = plan([flake(location="alpha.spec.mjs:120")], [issue("ABC-9", 'Flaky: "alpha › first screen"')])
    assert by_name["key"] == "ABC-9", "the line moved: the name still finds the issue"
    [urgent] = plan([flake()], [issue("ABC-476", "alpha.spec.mjs:95", priority="urgent")])
    assert urgent["urgent"] is True and "Urgent already" in urgent["body"]
    [best] = plan([flake()], [issue("ABC-8", "alpha › first screen", created="2026-11-01"),
                              issue("ABC-9", "alpha.spec.mjs:95", created="2026-11-09")])
    assert best["key"] == "ABC-9", "the file:line names it better than the name alone"


def test_an_issue_that_names_the_test_in_passing_or_another_line_or_file_takes_no_flake():
    """An issue names `beta.spec.mjs:138` in its description only; an issue about :135 is not about :13;
    beta.spec.mjs is not account-beta.spec.mjs. Each flake then gets its own new issue."""
    for other in (issue("ABC-481", "A nightly hunt", "The flake of `beta.spec.mjs:138` and alpha › first screen"),
                  issue("ABC-1", "beta.spec.mjs:1380 is flaky"), issue("ABC-2", "beta.spec.mjs:13 is flaky"),
                  issue("ABC-3", "account-beta.spec.mjs:138 is flaky"), issue("ABC-4", "pw/x-beta.spec.mjs:138")):
        [action] = plan([flake("beta.spec.mjs:138", "beta › a test")], [other])
        assert action["action"] == "create", other["title"]
    [found] = plan([flake("beta.spec.mjs:138", "beta › a test")], [issue("ABC-5", "app/tests/beta.spec.mjs:138: flaky")])
    assert found["key"] == "ABC-5", "a path before the file is a whole token still"
    [found] = plan([flake("beta.spec.mjs:138", "beta › a test")], [issue("ABC-6", "`beta.spec.mjs:138` (on CI)")])
    assert found["key"] == "ABC-6"


def test_a_name_inside_a_longer_name_takes_no_flake():
    """A test's name is the start of another test's name (`:5` and `:11` of
    epsilon.spec.mjs, a describe and a longer describe in eta.spec.mjs). The name counts only
    where it ends, at a closing quote or at the title's end."""
    short = flake("epsilon.spec.mjs:5", "Epsilon draws the items")
    longer = issue("ABC-11", 'epsilon.spec.mjs:11 "Epsilon draws the items on a phone" is flaky on CI')
    [action] = plan([short], [longer])
    assert action["action"] == "create", "the :11 issue is not about :5"
    [action] = plan([flake("eta.spec.mjs:7", "on a phone")],
                    [issue("ABC-12", 'Flaky: "on a phone, the tab strip"')])
    assert action["action"] == "create"
    for title in ('Flaky: "Epsilon draws the items"', "Epsilon draws the items", "`Epsilon draws the items` again",
                  "epsilon.spec.mjs:9 \u201cEpsilon draws the items\u201d"):
        [found] = plan([short], [issue("ABC-13", title)])
        assert found.get("key") == "ABC-13", title
    [found] = plan([short], [issue("ABC-14", "Epsilon draws the itemsheet")])
    assert found["action"] == "create", "a longer word is another name"
    # A name that is the end of another test's name (theta.spec.mjs:126 and iota.spec.mjs:429)
    load = "a failed load is described, and Try again loads the page"
    [action] = plan([flake("theta.spec.mjs:126", load)],
                    [issue("ABC-15", f'iota.spec.mjs:429 "Zeta: {load}" is flaky on CI')])
    assert action["action"] == "create", "the name starts at an opening quote or the title's start"


def test_a_duplicate_issue_takes_no_flake():
    """Linear lists the state Duplicate as open; nobody reads a comment there."""
    dup = {**issue("ABC-566", "delta.spec.mjs:9 is flaky"), "state": "Duplicate"}
    linked = {**issue("ABC-567", "delta.spec.mjs:9 is flaky"), "duplicate_of": "ABC-596"}
    [action] = plan([flake("delta.spec.mjs:9", "k")], [dup, linked])
    assert action["action"] == "create"
    [action] = plan([flake("delta.spec.mjs:9", "k")], [dup, {**issue("ABC-596", "delta.spec.mjs:9 flaky"), "state": "Backlog"}])
    assert action["key"] == "ABC-596", "Backlog counts as open"


def test_two_flakes_in_one_run_get_one_action_each_and_a_repeat_one():
    found = [flake(), flake("kappa.spec.mjs:40", "kappa"), flake()]
    actions = plan(found, [issue("ABC-476", "kappa.spec.mjs:40 flaky")])
    assert [(a["action"], a.get("key")) for a in actions] == [("create", None), ("comment", "ABC-476")]


def test_a_run_with_no_flake_does_nothing(tmp_path: Path, capsys):
    assert plan([], [issue("ABC-1", "x")]) == []
    empty = tmp_path / "flakes.json"
    empty.write_text("[]")
    args = ["flakes.py", str(empty), "--team", "ABC", "--run", RUN, "--head", HEAD, "--branch", "main"]
    assert flakes.main(args, post=lambda q, v: (_ for _ in ()).throw(AssertionError("no call"))) == 0
    assert flakes.main([args[0], str(tmp_path / "missing.json"), *args[2:]], environ={}) == 0, "the web job did not run"
    assert "no flaky test" in capsys.readouterr().out


def test_a_test_that_did_not_run_is_filed_as_a_flaky_test_is_with_the_error_outside_it(tmp_path: Path):
    """Gates-retry reruns a web job in which no test failed after its retry, so the run's first attempt files
    what stopped it. On 2026-11-16 a test of lambda.spec.mjs did not run after `route.fetch: Test
    ended`, outside any test."""
    counts = {"status": "failed", "total": 694, "unexpected": [], "interrupted": 0,
              "didNotRun": [{"name": "a saved item stays first", "file": "tests/pw/lambda.spec.mjs",
                             "line": 295, "location": "lambda.spec.mjs:295"}],
              "errors": ["route.fetch: Test ended.\nCall log:"]}
    [entry] = flakes.not_run(counts)
    assert entry["location"] == "lambda.spec.mjs:295" and "route.fetch: Test ended." in entry["error"]
    [action] = plan([entry], [])
    assert action["action"] == "create" and action["title"].startswith('lambda.spec.mjs:295 "a saved item')
    assert "did not run on CI" in action["body"] and "route.fetch: Test ended." in action["body"]
    [again] = plan([entry], [issue("ABC-919", 'lambda.spec.mjs:295 "a saved item stays first" did not run')])
    assert again["action"] == "comment" and again["key"] == "ABC-919"
    [alone] = flakes.not_run(counts | {"didNotRun": [], "errors": ["Error: worker crashed\nmore"]})
    assert alone["name"] == flakes.OUTSIDE and alone["location"] == "Error: worker crashed", "an error alone is filed too"
    assert flakes.not_run(None) == [] and flakes.not_run(counts | {"didNotRun": [], "errors": []}) == []
    # Only a run that gates-retry may rerun; any other is a real failure, and its did-not-run tests no flakes
    for why, run in (("a test failed after its retry", counts | {"unexpected": [{"location": "x.spec.mjs:1"}]}),
                     ("a build that failed: no test ran", counts | {"total": 0, "didNotRun": [],
                                                                     "errors": ["the build failed (exit 1): x"]}),
                     ("an interrupted run (the job's time-out)", counts | {"status": "interrupted", "interrupted": 3}),
                     ("an interrupted test", counts | {"interrupted": 1}),
                     ("every test did not run", counts | {"total": 1}),
                     ("the global time-out", counts | {"status": "timedout"})):
        assert flakes.not_run(run) == [], why
    # main() reads run.json beside the file of flaky tests, also when no test was flaky
    (tmp_path / "flakes.json").write_text("[]")
    (tmp_path / "run.json").write_text(json.dumps(counts))
    calls: list[str] = []

    def post(query: str, variables: dict[str, Any]) -> dict[str, Any]:
        calls.append(query)
        return {"team": {"issues": {"nodes": [], "pageInfo": {"hasNextPage": False}}}}

    args = ["flakes.py", str(tmp_path / "flakes.json"), "--team", "ABC", "--run", RUN, "--head", HEAD, "--branch",
            "abc-12-x", "--dry-run"]
    flakes.main(args, post=post, environ={"LINEAR_API_KEY": "k"})
    assert calls, "a test that did not run is no empty run: Linear is read"


def test_the_origin_line_names_the_pull_requests_issue_or_main():
    assert flakes.origin("abc-12-thing", "ABC-12") == "Found in ABC-12"
    assert flakes.origin("main", None) == "Found on main"
    assert flakes.origin("no-key", None) == "Found in the branch no-key"
    # gates.yml also runs by hand (workflow_dispatch) on a branch; the origin goes by the branch alone
    assert flakes.origin("abc-12-thing", "ABC-12") != "Found on main"


class FakeLinear:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def comment(self, key: str, body: str) -> None:
        self.calls.append(("comment", key))

    def issue(self, key: str) -> dict[str, Any]:
        return {"id": f"id-{key}"}

    def post(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("priority", variables["id"], variables["p"]))
        return {"issueUpdate": {"success": True}}

    def create(self, team: str, title: str, body: str, labels: list[str], state: str | None, **more: Any) -> dict[str, Any]:
        self.calls.append(("create", team, tuple(labels), state, more["priority"], more["size"]))
        return {"identifier": "ABC-99", "url": "https://linear.app/x"}


def test_apply_comments_raises_the_priority_once_and_files_in_todo_with_the_fast_lane():
    client = FakeLinear()
    actions = plan([flake(), flake("a.spec.mjs:1", "a")], [issue("ABC-476", "alpha.spec.mjs:95")])
    actions.append({"action": "comment", "key": "ABC-5", "urgent": True, "body": "b"})
    done = flakes.apply(client, "ABC", actions, {"urgent": 1})
    assert client.calls == [("comment", "ABC-476"), ("priority", "id-ABC-476", 1),
                            ("create", "ABC", ("fast-lane", "frontend"), "Todo", "high", "S"), ("comment", "ABC-5")]
    assert done == ["commented on ABC-476, now Urgent", "created ABC-99 https://linear.app/x", "commented on ABC-5"]


def test_the_workflow_runs_mains_script_after_each_gates_run_with_the_linear_key():
    path = REPO / ".github" / "workflows" / "flakes.yml"
    flow = yaml.safe_load(path.read_text())
    assert flow[True] == {"workflow_run": {"workflows": ["gates"], "types": ["completed"]}}
    job = flow["jobs"]["file"]
    assert job["if"] == ("github.event.workflow_run.conclusion == 'failure' || "
                         "(github.event.workflow_run.conclusion == 'success' && (github.event.workflow_run.event == 'schedule' || "
                         "github.event.workflow_run.event == 'workflow_dispatch') && github.event.workflow_run.head_branch == 'main')"), \
        "a flake fails a pull request's run, and a run on main passes with it"
    assert job["permissions"] == {"actions": "read", "contents": "read"} and flow["permissions"] == {"contents": "read"}
    checkout = next(s for s in job["steps"] if s.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["ref"] == "${{ github.event.repository.default_branch }}", "main's script, never a branch's"
    step = next(s for s in job["steps"] if "run" in s)
    assert step["run"] == "bash .github/flakes-file.sh", "One call of the script, which main's checkout holds"
    assert step["env"]["LINEAR_API_KEY"] == "${{ secrets.LINEAR_API_KEY }}"
    script = (REPO / ".github" / "flakes-file.sh").read_text()
    assert 'flakes-$ATTEMPT' in script and '--team "${LINEAR_TEAM:-}" --repo "$GITHUB_REPOSITORY" ' in script, \
        "no team of its own: the product's AGENTS.md names it"
    json.dumps(flow[True])


TEAMS_MD = """# Acme

## The teams

| Team | Repository | The owner's checkout | The Linear key |
|---|---|---|---|
| ABC | acme/product | `~/dev/product` | `LINEAR_API_KEY` |
| XYZ | acme/tools | `~/dev/tools` | `LINEAR_API_KEY` |

## Rules

| QQQ | acme/other | x | y |
"""


def test_the_team_is_the_row_of_the_repository_in_the_teams():
    assert flakes.team_of(TEAMS_MD, "acme/tools") == "XYZ" and flakes.team_of(TEAMS_MD, "Acme/Product") == "ABC"
    assert flakes.team_of(TEAMS_MD, "acme/other") == "", "a table outside \"The teams\" names no team"
    assert flakes.team_of("# no table\n", "acme/product") == ""


def test_a_run_with_no_team_reads_agents_md_and_stops_when_no_row_names_the_repository(tmp_path, capsys):
    (tmp_path / "flakes.json").write_text("[]")
    agents_md = tmp_path / "AGENTS.md"
    agents_md.write_text(TEAMS_MD)
    args = ["flakes.py", str(tmp_path / "flakes.json"), "--team", "", "--run", RUN, "--head", HEAD, "--branch", "main"]
    assert flakes.main([*args, "--repo", "acme/tools"], environ={}, agents_md=agents_md) == 0
    assert flakes.main([*args, "--repo", "acme/nobody"], environ={}, agents_md=agents_md) == 1
    assert 'no row of' in capsys.readouterr().err
    assert flakes.main(args, environ={}, agents_md=tmp_path / "missing.md") == 1, "no repository, no file: no team"
