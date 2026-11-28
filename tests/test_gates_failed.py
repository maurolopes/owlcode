"""Gates that did not pass on an approved head (.github/gates_failed.py and .github/workflows/gates-failed.yml, OWL-30). The check
`verdict` holds only the review, so a head that the review approved and whose required gate failed (or was skipped, or
was cancelled) is sent back to an implementer by this workflow: one comment for each head, and the issue to In Progress. The
script runs with gh and linear.py faked, never the network."""

import importlib.util
import json
import sys
from types import ModuleType

import pytest
import yaml

from tests.conftest import REAL_REPO
from tests.test_verdict import verdict

WORKFLOW = REAL_REPO / ".github" / "workflows" / "gates-failed.yml"
HEAD = "a" * 40
ENV = {"GITHUB_REPOSITORY": "owner/repo", "GH_TOKEN": "t", "LINEAR_API_KEY": "key"}


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("github_gates_failed", REAL_REPO / ".github" / "gates_failed.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gates = load()


def run(name: str, conclusion: str | None, ident: int, status: str = "COMPLETED") -> dict:
    """One check run as the query reads it; a higher id is a newer run."""
    return {"databaseId": ident, "name": name, "status": status, "conclusion": conclusion, "detailsUrl": f"https://run/{ident}"}


def pull(number: int = 7, runs: list[dict] | None = None, draft: bool = False, labels: tuple[str, ...] = (),
         head: str = HEAD) -> dict:
    """One open pull request as the query reads it: by default approved (`verdict` passed) with its `python` failed."""
    contexts = runs if runs is not None else [run("verdict", "SUCCESS", 20), run("python", "FAILURE", 10)]
    return {"number": number, "title": "ABC-7: a change", "isDraft": draft, "headRefOid": head, "headRef": {"name": "abc-7-change"},
            "labels": {"nodes": [{"name": n} for n in labels]},
            "commits": {"nodes": [{"commit": {"statusCheckRollup": {"contexts": {"nodes": contexts}}}}]}}


def reply(*pulls: dict) -> dict:
    return {"data": {"repository": {"pullRequests": {"nodes": list(pulls)}}}}


class Tools:
    """gh and linear.py faked: each call is logged, and the first rule whose text is in the call answers (code, output)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, rules: list[tuple[str, int, str]]):
        self.calls: list[str] = []
        self.rules = rules
        monkeypatch.setattr(gates, "gh", lambda *args: self.answer(" ".join(args)))
        monkeypatch.setattr(gates, "linear", lambda *args: self.answer("linear " + " ".join(args)))

    def answer(self, call: str) -> gates.Result:
        self.calls.append(call)
        for text, code, out in self.rules:
            if text in call:
                return gates.Result(code, out)
        return gates.Result(0, "ABC-7\n" if call.startswith("linear key-from") else "")

    def of(self, start: str) -> list[str]:
        return [c for c in self.calls if c.startswith(start)]


def graphql(*pulls: dict) -> tuple[str, int, str]:
    return "api graphql", 0, json.dumps(reply(*pulls))


def test_a_failed_gate_on_an_approved_head_gives_one_comment_and_the_issue_goes_to_in_progress(monkeypatch, capsys):
    """The Done-when of OWL-30: a failed `python` on an approved head gives one FAILED_GATES comment and the issue to In
    Progress. The comment's first words are verdict.py's (the review counts its rounds again from it), then the head."""
    tools = Tools(monkeypatch, [graphql(pull())])
    assert gates.check(ENV) == 0
    (comment,) = tools.of("pr comment")
    body = comment.split("--body ", 1)[1]
    assert comment.startswith("pr comment 7 -R owner/repo") and body.startswith(f"{verdict.FAILED_GATES} {HEAD}:"), body
    assert "the check python ended with failure (https://run/10)" in body and "round 1" in body
    assert tools.of("linear") == ["linear key-from abc-7-change ABC-7: a change", "linear state ABC-7 In Progress"]
    assert f"::notice::PR #7: gates did not pass on the approved head {HEAD}: python" in capsys.readouterr().out


def test_a_passed_run_gives_nothing(monkeypatch, capsys):
    """A run that passed, one whose gate is neutral, a gate that is not on the head (a conflict gets no gates run), and a
    gate that still runs say nothing: its end starts the workflow again."""
    passed = [run("verdict", "SUCCESS", 20), run("python", "SUCCESS", 10)]
    cases = {"passed": passed, "neutral": [run("verdict", "SUCCESS", 20), run("python", "NEUTRAL", 10)],
             "no gate": [run("verdict", "SUCCESS", 20)], "running": [run("verdict", "SUCCESS", 20), run("python", None, 10, "IN_PROGRESS")],
             "queued": [run("verdict", "SUCCESS", 20), run("python", None, 10, "QUEUED")], "no checks": []}
    for name, runs in cases.items():
        tools = Tools(monkeypatch, [graphql(pull(runs=runs))])
        assert gates.check(ENV) == 0, name
        assert not tools.of("pr comment") and not tools.of("linear"), name
    assert "no approved pull request has a gate of python web that did not pass" in capsys.readouterr().out


def test_a_second_run_on_the_same_head_gives_no_second_comment(monkeypatch, capsys):
    """Each head has its comment once: a rerun of the gates that fails again, or the end of a review after the end of the
    gates, finds the words of the head in the pull request's comments and says nothing, and the issue stays as it is."""
    first = Tools(monkeypatch, [graphql(pull())])
    gates.check(ENV)
    (comment,) = first.of("pr comment")
    posted = comment.split("--body ", 1)[1]
    second = Tools(monkeypatch, [("issues/7/comments", 0, f"An older comment\n{posted}\n"), graphql(pull())])
    assert gates.check(ENV) == 0
    assert not second.of("pr comment") and not second.of("linear")
    assert f"::notice::PR #7: the head {HEAD} has its comment already" in capsys.readouterr().out
    # a new head (a fix was pushed) is another head: it gets its own comment
    other = "b" * 40
    third = Tools(monkeypatch, [("issues/7/comments", 0, posted), graphql(pull(head=other))])
    gates.check(ENV)
    assert len(third.of("pr comment")) == 1 and f"{verdict.FAILED_GATES} {other}:" in third.of("pr comment")[0]


@pytest.mark.parametrize("conclusion", ["FAILURE", "CANCELLED", "TIMED_OUT", "SKIPPED", "ACTION_REQUIRED", "STARTUP_FAILURE"])
def test_a_gate_that_ended_with_no_pass_counts_a_skipped_one_too(monkeypatch, conclusion):
    """GitHub counts a skipped required check as passed, so a gate that is skipped is a gate that did not run (the gates
    workflow never skips a gate job as a whole, so a skip here is a failure of the workflow's own `changes`)."""
    tools = Tools(monkeypatch, [graphql(pull(runs=[run("verdict", "SUCCESS", 20), run("python", conclusion, 10)]))])
    gates.check(ENV)
    assert f"the check python ended with {conclusion.lower()}" in tools.of("pr comment")[0]
    assert "linear state ABC-7 In Progress" in tools.calls


def test_only_a_ready_pull_request_that_the_review_approved_is_sent_back(monkeypatch):
    """A draft is in its fix round; a head with no `verdict`, with a failed or running one is not approved (the review
    decides it first: a request for changes goes to the implementer by itself, and an approve starts the workflow again)."""
    cases = {"draft": pull(draft=True),
             "no verdict": pull(runs=[run("python", "FAILURE", 10)]),
             "failed verdict": pull(runs=[run("verdict", "FAILURE", 20), run("python", "FAILURE", 10)]),
             "running verdict": pull(runs=[run("verdict", None, 20, "IN_PROGRESS"), run("python", "FAILURE", 10)])}
    for name, one in cases.items():
        tools = Tools(monkeypatch, [graphql(one)])
        assert gates.check(ENV) == 0
        assert not tools.of("pr comment") and not tools.of("linear"), name


def test_the_newest_run_of_a_gate_decides(monkeypatch):
    """A rerun, or a run that a newer push cancelled, leaves an older run of the check on the head: the highest id is the newest."""
    healed = [run("verdict", "SUCCESS", 30), run("python", "FAILURE", 10), run("python", "SUCCESS", 15)]
    tools = Tools(monkeypatch, [graphql(pull(runs=healed))])
    gates.check(ENV)
    assert not tools.of("pr comment"), "a rerun passed after the failed run"
    broken = [run("verdict", "SUCCESS", 30), run("python", "SUCCESS", 10), run("python", "FAILURE", 15)]
    tools = Tools(monkeypatch, [graphql(pull(runs=broken))])
    gates.check(ENV)
    assert len(tools.of("pr comment")) == 1, "a failed rerun after a run that passed"


def test_each_pull_request_is_read_and_the_gates_come_from_the_environment(monkeypatch):
    """The workflow reads every open pull request, so the approve and the failed gate come in any order. GATES_CHECKS names
    another product's gates (the default is `python web`): `web` failing sends a head back, and `unit` is not a gate there."""
    web = [run("verdict", "SUCCESS", 20), run("python", "SUCCESS", 10), run("web", "FAILURE", 11)]
    tools = Tools(monkeypatch, [graphql(pull(7, runs=web), pull(8, runs=[run("verdict", "SUCCESS", 20), run("python", "SUCCESS", 10)]),
                                        pull(9, head="c" * 40))])
    gates.check(ENV)
    assert [c.split()[2] for c in tools.of("pr comment")] == ["7", "9"] and "the check web ended with failure" in tools.of("pr comment")[0]
    other = [run("verdict", "SUCCESS", 20), run("python", "FAILURE", 10), run("unit", "SUCCESS", 11)]
    tools = Tools(monkeypatch, [graphql(pull(runs=other))])
    gates.check({**ENV, "GATES_CHECKS": "unit"})
    assert not tools.of("pr comment"), "`python` is not a gate of this product"
    tools = Tools(monkeypatch, [("api graphql", 4, "")])
    assert gates.check(ENV) == 4 and not tools.of("pr comment"), "a failed read fails the job"


def test_a_merged_or_closed_pull_request_keeps_its_issue(monkeypatch, capsys):
    """A pull request that merged or closed since the read gets no comment and no move (once, a review that ended after
    the merge moved a Done issue back). A state that gh cannot read counts as open."""
    for state, sent in (("MERGED", False), ("CLOSED", False), ("OPEN", True)):
        tools = Tools(monkeypatch, [("--json state", 0, state + "\n"), graphql(pull())])
        gates.check(ENV)
        assert bool(tools.of("pr comment")) is sent and ("linear state ABC-7 In Progress" in tools.calls) is sent, state
    assert "::notice::PR #7 is closed: its issue stays as it is" in capsys.readouterr().out
    tools = Tools(monkeypatch, [("--json state", 1, ""), graphql(pull())])
    gates.check(ENV)
    assert tools.of("pr comment") and "linear state ABC-7 In Progress" in tools.calls


def test_a_failed_read_or_write_changes_nothing_else_and_the_next_run_tries_again(monkeypatch, capsys):
    """Without the comment nothing else changes (no label, no move), so the next run writes it. Comments that cannot be read
    leave the head for the next run too, so one head never gets two comments."""
    tools = Tools(monkeypatch, [("issues/7/comments", 1, ""), graphql(pull(labels=("escalated",)))])
    assert gates.check(ENV) == 0
    assert not tools.of("pr comment") and not tools.of("linear") and "-X DELETE" not in " ".join(tools.calls)
    tools = Tools(monkeypatch, [("pr comment", 1, ""), graphql(pull(labels=("escalated",)))])
    assert gates.check(ENV) == 0
    assert not tools.of("linear") and "-X DELETE" not in " ".join(tools.calls)
    assert "::warning::PR #7: the comment on the gates failed" in capsys.readouterr().out


def test_the_label_escalated_goes_off_and_the_move_needs_a_key_and_a_linear_that_answers(monkeypatch, capsys):
    """A pull request that waited for the owner's merge (a code owner's file) waits on its session now, as for a conflict:
    the label goes off. The move needs the issue's key in the branch or the title, and the Linear key."""
    tools = Tools(monkeypatch, [graphql(pull(labels=("escalated", "workflow")))])
    gates.check(ENV)
    assert "api -X DELETE repos/owner/repo/issues/7/labels/escalated" in tools.calls
    tools = Tools(monkeypatch, [graphql(pull())])
    gates.check(ENV)
    assert not any("-X DELETE" in c for c in tools.calls), "no label, no call"
    tools = Tools(monkeypatch, [("-X DELETE", 1, ""), graphql(pull(labels=("escalated",)))])
    gates.check(ENV)
    assert "::warning::PR #7: the label escalated could not be removed: remove it by hand" in capsys.readouterr().out
    assert "linear state ABC-7 In Progress" in tools.calls, "a failed label only warns"
    tools = Tools(monkeypatch, [graphql(pull())])
    gates.check({**ENV, "LINEAR_API_KEY": ""})
    assert tools.of("pr comment") and not tools.of("linear state"), "no Linear key, no move"
    tools = Tools(monkeypatch, [("key-from", 0, "\n"), graphql(pull())])
    gates.check(ENV)
    assert not tools.of("linear state"), "no issue key in the branch or the title"
    tools = Tools(monkeypatch, [("linear state", 1, ""), graphql(pull())])
    gates.check(ENV)
    assert "::warning::the move of ABC-7 to In Progress failed: move it by hand" in capsys.readouterr().out


def test_the_command_line_takes_check_only(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(gates, "check", lambda env: seen.append(env) or 0)
    assert gates.main(["check"]) == 0 and seen == [gates.os.environ]
    assert gates.main([]) == 2 and gates.main(["check", "x"]) == 2 and gates.main(["failed"]) == 2
    assert "usage: gates_failed.py check" in capsys.readouterr().err


def test_the_workflow_runs_mains_code_on_the_end_of_a_gates_run_or_a_review_one_run_at_a_time():
    """The workflow starts on the end of a gates run that did not pass (never a cancelled one: a newer run of the same group is
    not on the head yet) and on the end of a review that ran a job (an approve that came after the failed gate), and by hand.
    One concurrency group, with no cancel: each run reads every pull request, so the second finds the first's comment. The
    job checks out main only and holds the implementer app's token with no write to code (agents/README.md "The guards")."""
    flow = yaml.safe_load(WORKFLOW.read_text())
    on = flow[True] if True in flow else flow["on"]
    assert on["workflow_run"] == {"workflows": ["gates", "Claude Code Review"], "types": ["completed"]}
    assert "workflow_dispatch" in on and "pull_request" not in on and "pull_request_target" not in on and "push" not in on
    for name, file in (("gates", "gates.yml"), ("Claude Code Review", "claude-code-review.yml")):
        assert yaml.safe_load((REAL_REPO / ".github" / "workflows" / file).read_text())["name"] == name
    assert flow["permissions"] == {"contents": "read"}
    job = flow["jobs"]["failed"]
    assert job["concurrency"] == {"group": "gates-failed", "cancel-in-progress": False}
    assert job["if"].split() == (
        "vars.IMPLEMENTER_APP_CLIENT_ID != '' && (github.event_name == 'workflow_dispatch' "
        "|| (github.event.workflow_run.path == '.github/workflows/gates.yml' && github.event.workflow_run.conclusion != 'success' "
        "&& github.event.workflow_run.conclusion != 'cancelled') "
        "|| (github.event.workflow_run.path == '.github/workflows/claude-code-review.yml' && github.event.workflow_run.conclusion != 'skipped'))").split()
    checkout, token, step = job["steps"]
    assert checkout["with"]["ref"] == "${{ github.event.repository.default_branch }}", "main's code only"
    assert token["with"]["permission-pull-requests"] == "write" and token["with"]["permission-checks"] == "read"
    assert "permission-contents" not in token["with"] and token["with"]["client-id"] == "${{ vars.IMPLEMENTER_APP_CLIENT_ID }}"
    assert step["run"].strip() == "python3 -I .github/gates_failed.py check", "the logic lives in the script"
    assert step["env"] == {"GH_TOKEN": "${{ steps.token.outputs.token }}", "LINEAR_API_KEY": "${{ secrets.LINEAR_API_KEY }}"}


def test_the_comment_is_the_one_the_review_counts_its_rounds_from():
    """verdict.py's FAILED_GATES is the first words of the comment, and the implementer app's comment restarts the rounds."""
    assert gates.FAILED_GATES == verdict.FAILED_GATES
    comment = {"body": f"{gates.words(HEAD)} the check python ended with failure.", "user": {"login": "u[bot]"}}
    assert verdict.restarts(comment, "r[bot]", "u[bot]") is True
