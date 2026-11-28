""".workflow/github_setup.py (`owlcode github-setup`): a product's repository set up the way the loop needs it. A fake `gh`
records each write and sends nothing."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / ".workflow" / "github_setup.py"
SPEC = importlib.util.spec_from_file_location("github_setup", SCRIPT)
assert SPEC and SPEC.loader
setup = importlib.util.module_from_spec(SPEC)
sys.modules["github_setup"] = setup
SPEC.loader.exec_module(setup)

NAME = "acme/product"
MERGES = {"allow_merge_commit": True, "allow_squash_merge": False, "allow_rebase_merge": False, "delete_branch_on_merge": True,
          "allow_auto_merge": True, "allow_update_branch": False, "private": True}


class FakeGitHub:
    """The replies by path; every write is recorded, never sent."""

    def __init__(self, replies: dict[str, Any]) -> None:
        self.replies = replies
        self.writes: list[tuple[str, str, Any]] = []

    def __call__(self, path: str, *args: str, body: dict[str, Any] | None = None, allow_404: bool = False) -> Any:
        if "--method" in args:
            self.writes.append((args[args.index("--method") + 1], path, body))
            return {}
        return self.replies.get(path)


def run(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], replies: dict[str, Any], *args: str) -> tuple[FakeGitHub, str, int]:
    fake = FakeGitHub({f"repos/{NAME}": dict(MERGES), **replies})
    monkeypatch.setattr(setup, "gh", fake)
    opened: list[str] = []
    monkeypatch.setattr(setup.webbrowser, "open", opened.append)
    code = setup.main([NAME, *args])
    out = capsys.readouterr().out
    return fake, out + "".join(f"opened {u}\n" for u in opened), code


def test_a_new_ruleset_requires_the_checks_without_an_up_to_date_branch(monkeypatch, capsys) -> None:
    fake, said, code = run(monkeypatch, capsys, {}, "--check", "python", "--check", "verdict", "--code-owners", "--bypass-admin",
                           "--auto-merge", "--no-browser")
    assert code == 0 and "ruleset: creating 'main'" in said and "adds check verdict" in said
    [(method, path, body)] = fake.writes
    assert (method, path) == ("POST", f"repos/{NAME}/rulesets")
    checks = next(r for r in body["rules"] if r["type"] == "required_status_checks")["parameters"]
    assert checks["strict_required_status_checks_policy"] is False, "pull requests merge in parallel (agents/README.md)"
    assert [c["context"] for c in checks["required_status_checks"]] == ["python", "verdict"]
    review = next(r for r in body["rules"] if r["type"] == "pull_request")["parameters"]
    assert review["require_code_owner_review"] is True and review["allowed_merge_methods"] == ["merge"]
    assert body["bypass_actors"] == [setup.ADMIN]


def test_an_existing_ruleset_keeps_the_owners_rules_and_its_checks_without_check(monkeypatch, capsys) -> None:
    current = {"id": 7, "name": "main", "enforcement": "active", "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
               "bypass_actors": [{"actor_id": 9, "actor_type": "Integration", "bypass_mode": "always"}],
               "rules": [{"type": "required_signatures"}, {"type": "required_status_checks", "parameters": {
                   "strict_required_status_checks_policy": False, "do_not_enforce_on_create": False,
                   "required_status_checks": [{"context": "python", "integration_id": 1}]}}]}
    replies = {f"repos/{NAME}/rulesets": [{"id": 7, "name": "main"}], f"repos/{NAME}/rulesets/7": current}
    fake, said, _ = run(monkeypatch, capsys, replies, "--auto-merge", "--no-browser")
    assert "keeps rule required_signatures (not managed by this script)" in said
    assert "keeps the bypass list (Integration 9; not managed by this script)" in said
    [(method, path, body)] = fake.writes
    assert (method, path) == ("PUT", f"repos/{NAME}/rulesets/7")
    assert {"type": "required_signatures"} in body["rules"]
    kept = next(r for r in body["rules"] if r["type"] == "required_status_checks")
    assert kept["parameters"]["required_status_checks"] == [{"context": "python", "integration_id": 1}]
    fake, said, _ = run(monkeypatch, capsys, replies, "--no-checks", "--auto-merge", "--no-browser")
    assert "removes check python" in said
    assert not any(r["type"] == "required_status_checks" for r in fake.writes[0][2]["rules"])


def test_a_ruleset_that_matches_sends_nothing(monkeypatch, capsys) -> None:
    wanted = setup.ruleset(["python", "verdict"], code_owners=True, bypass_admin=True)
    replies = {f"repos/{NAME}/rulesets": [{"id": 3, "name": "main"}], f"repos/{NAME}/rulesets/3": {"id": 3, **wanted}}
    fake, said, code = run(monkeypatch, capsys, replies, "--check", "python", "--check", "verdict", "--code-owners", "--bypass-admin",
                           "--auto-merge", "--no-browser")
    assert code == 0 and fake.writes == []
    assert "ruleset: unchanged (required checks: python, verdict)" in said
    assert "merges: already merge commits only" in said


def test_the_merges_change_only_the_keys_that_differ_and_leave_the_update_button(monkeypatch, capsys) -> None:
    fake, said, _ = run(monkeypatch, capsys, {f"repos/{NAME}": {**MERGES, "allow_squash_merge": True, "allow_auto_merge": True}},
                        "--no-browser")
    patch = next(body for method, path, body in fake.writes if method == "PATCH")
    assert patch == {"allow_squash_merge": False, "allow_auto_merge": False}
    assert "merges: allow_squash_merge True -> False" in said


def test_each_variable_is_made_changed_or_left_and_a_bad_one_is_refused(monkeypatch, capsys) -> None:
    replies = {f"repos/{NAME}/actions/variables/B": {"value": "old"}, f"repos/{NAME}/actions/variables/C": {"value": "same"}}
    fake, said, _ = run(monkeypatch, capsys, replies, "--variable", "A=1", "--variable", "B=new", "--variable", "C=same", "--no-browser")
    variables = [(m, p) for m, p, _ in fake.writes if "variables" in p]
    assert variables == [("POST", f"repos/{NAME}/actions/variables"), ("PATCH", f"repos/{NAME}/actions/variables/B")]
    assert "variable A: new, 1" in said and "variable B: old -> new" in said and "variable C: already same" in said
    with pytest.raises(SystemExit) as refused:
        setup.main([NAME, "--variable", "no-equals"])
    assert refused.value.code == 2


def test_the_apps_are_the_ones_named_and_none_is_built_in(monkeypatch, capsys) -> None:
    _, said, _ = run(monkeypatch, capsys, {}, "--app", "acme-product-implementer", "--app", "acme-product-reviewer")
    assert "opened https://github.com/apps/acme-product-implementer/installations/new" in said
    assert "app acme-product-reviewer: tick 'product' under 'Only select repositories'" in said
    _, said, _ = run(monkeypatch, capsys, {}, "--no-browser")
    assert "apps: none named" in said and "github.com/apps/" not in said


def test_the_dry_run_reads_and_prints_and_sends_and_opens_nothing(monkeypatch, capsys) -> None:
    fake, said, code = run(monkeypatch, capsys, {f"repos/{NAME}": {**MERGES, "allow_squash_merge": True}}, "--dry-run", "--check", "python",
                           "--variable", "A=1", "--app", "acme-product-reviewer")
    assert code == 0 and fake.writes == []
    assert "ruleset: would create 'main'" in said and "variable A: new, 1" in said and "opened" not in said
    assert said.rstrip().endswith("dry run: nothing changed")


def test_no_environment_step_and_no_secret_flag() -> None:
    """The loop's keys stay repository secrets: the review workflow runs on `pull_request` and reads them there."""
    text = SCRIPT.read_text()
    assert "environment" not in text and "--no-environment" not in text and "secret set" not in text


@pytest.mark.skipif(not Path("/usr/bin/python3").exists() or not shutil.which("git"), reason="the Mac's Python")
def test_the_script_runs_on_the_macs_python() -> None:
    done = subprocess.run(["/usr/bin/python3", "-I", str(SCRIPT), "--help"], capture_output=True, text=True, check=False)
    assert done.returncode == 0 and "OWNER/REPO" in done.stdout


def test_gh_reads_a_404_only_when_allowed_and_names_the_paid_plan(monkeypatch) -> None:
    """`gh` itself, with `subprocess.run` replaced: the steps that read a missing variable or repository rely on its 404 rule."""
    replies: list[subprocess.CompletedProcess[str]] = []
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return replies.pop(0)

    monkeypatch.setattr(setup.subprocess, "run", fake_run)
    missing = '{"message": "Not Found", "status": "404"}'
    replies.append(subprocess.CompletedProcess([], 1, missing, ""))
    assert setup.gh(f"repos/{NAME}/actions/variables/A", allow_404=True) is None
    replies.append(subprocess.CompletedProcess([], 1, missing, ""))
    with pytest.raises(setup.Failed, match=r"failed: .*Not Found"):
        setup.gh(f"repos/{NAME}/actions/variables/A")
    replies.append(subprocess.CompletedProcess([], 1, '{"message": "Upgrade to GitHub Pro or make this repository public"}', ""))
    with pytest.raises(setup.Failed, match="paid plan"):
        setup.gh(f"repos/{NAME}/rulesets", "--method", "POST", body={"name": "main"})
    assert calls[-1][-2:] == ["--input", "-"], "a body goes on stdin"
    replies.append(subprocess.CompletedProcess([], 0, '{"value": "1"}', ""))
    assert setup.gh(f"repos/{NAME}/actions/variables/A") == {"value": "1"}
