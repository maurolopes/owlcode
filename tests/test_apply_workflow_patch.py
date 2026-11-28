"""The rule of .github/apply_workflow_patch.py and the wiring of the workflow apply-workflow-patch.yml."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml


REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("apply_workflow_patch", REPO / ".github" / "apply_workflow_patch.py")
assert spec and spec.loader
awp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(awp)

OWN = "- a\n+ b\n"
GOOD = (
    "diff --git a/.github/workflows/x.yml b/.github/workflows/x.yml\n"
    "--- a/.github/workflows/x.yml\n+++ b/.github/workflows/x.yml\n@@ -1 +1 @@\n-a\n+b\n"
)


APP = "owner-implementer[bot]"  # the default login when the owner is "owner": OWNER-implementer[bot]


def comment(body: str, login: str = APP, at: str = "2026-11-23T10:00:00Z", **more: Any) -> dict[str, Any]:
    return {"id": 1, "user": {"login": login}, "body": body, "created_at": at, "updated_at": at} | more


def fenced(patch: str) -> str:
    return f"The patch:\n\n```diff\n{patch}```\n"


def test_a_good_patch_of_the_implementer_app_applies():
    assert awp.decide([comment(fenced(GOOD))], "owner", "owner") == ("apply", GOOD)


def test_only_the_repository_owner_s_label_counts():
    assert awp.decide([comment(fenced(GOOD))], APP, "owner")[0] == "refuse"
    assert awp.decide([comment(fenced(GOOD))], "", "")[0] == "refuse"


@pytest.fixture(autouse=True)
def default_app(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test reads the default app unless it sets IMPLEMENTER_LOGIN itself."""
    monkeypatch.delenv("IMPLEMENTER_LOGIN", raising=False)


def test_the_default_app_is_the_owner_s_implementer():
    """With no IMPLEMENTER_LOGIN, the app is OWNER-implementer[bot]: the owner's shared pair keeps its name."""
    assert awp.implementer("acme") == "acme-implementer[bot]"
    assert awp.decide([comment(fenced(GOOD), login="acme-implementer[bot]")], "acme", "acme") == ("apply", GOOD)
    assert awp.decide([comment(fenced(GOOD))], "acme", "acme")[0] == "refuse", "another owner's app is a stranger"


def test_the_implementer_app_comes_from_implementer_login(monkeypatch: pytest.MonkeyPatch):
    """A repository with its own app pair sets IMPLEMENTER_LOGIN; then the shared pair's app is a stranger, and the refusal names the new one."""
    monkeypatch.setenv("IMPLEMENTER_LOGIN", "acme-implementer[bot]")
    assert awp.decide([comment(fenced(GOOD), login="acme-implementer[bot]")], "owner", "owner") == ("apply", GOOD)
    assert awp.decide([comment(fenced(GOOD))], "owner", "owner") == (
        "refuse", "no comment of acme-implementer[bot] holds a patch (a fenced block of diff)")


def test_a_stranger_s_comment_is_no_patch():
    assert awp.decide([comment(fenced(GOOD), login="stranger")], "owner", "owner")[0] == "refuse"


def test_a_path_outside_the_workflows_refuses_the_whole_patch():
    for bad in (".github/verdict.py", "AGENTS.md", ".github/workflows/../verdict.py", ".github/workflows/"):
        patch = GOOD + f"diff --git a/{bad} b/{bad}\n--- a/{bad}\n+++ b/{bad}\n@@ -1 +1 @@\n-a\n+b\n"
        assert awp.decide([comment(fenced(patch))], "owner", "owner")[0] == "refuse", bad
    rename = "diff --git a/.github/workflows/x.yml b/x.yml\nrename from .github/workflows/x.yml\nrename to x.yml\n"
    assert awp.decide([comment(fenced(rename))], "owner", "owner")[0] == "refuse"
    link = GOOD.replace("--- a/", "new file mode 120000\n--- a/", 1)
    assert awp.decide([comment(fenced(link))], "owner", "owner")[0] == "refuse"


def test_a_new_workflow_file_is_allowed():
    patch = "diff --git a/.github/workflows/n.yml b/.github/workflows/n.yml\nnew file mode 100644\n--- /dev/null\n+++ b/.github/workflows/n.yml\n@@ -0,0 +1 @@\n+a\n"
    assert awp.decide([comment(fenced(patch))], "owner", "owner")[0] == "apply"


def test_a_patch_after_the_label_is_not_what_the_owner_read_and_replaces_the_older_one():
    old = comment(fenced(GOOD), at="2026-11-23T10:00:00Z")
    new = comment(fenced(GOOD.replace("+b", "+c")), at="2026-11-23T12:00:00Z", id=2)
    assert awp.newest_patch([old, new], APP)[0] == "2"
    assert awp.newest_patch([old, new], APP, before="2026-11-23T13:00:00Z")[0] == "2"
    assert awp.newest_patch([old, new], APP, before="2026-11-23T11:00:00Z") is None, "the newer patch replaces the older: no fall back"
    edited = comment(fenced(GOOD), at="2026-11-23T10:00:00Z", updated_at="2026-11-23T12:00:00Z")
    assert awp.newest_patch([edited], APP, before="2026-11-23T11:00:00Z") is None


def test_no_patch_is_a_refusal():
    assert awp.decide([], "owner", "owner")[0] == "refuse"
    assert awp.decide([comment("no block here")], "owner", "owner")[0] == "refuse"
    assert awp.decide([comment(fenced("just words\n"))], "owner", "owner")[0] == "refuse", "a block with no diff header names no file"


def test_a_plain_list_of_comments_reads_like_pages():
    assert awp.flat([comment("a")]) == awp.flat([[comment("a")]]) and awp.flat({}) == []


def test_the_whole_run_applies_commits_and_pushes_in_a_scratch_repository(tmp_path: Path):
    """The script on a real repository with a bare remote: the file changes, one commit lands on the remote branch."""
    remote, work = tmp_path / "remote.git", tmp_path / "work"
    run = lambda *a, cwd=work: subprocess.run(a, cwd=cwd, check=True, capture_output=True, text=True)  # noqa: E731
    run("git", "init", "--bare", "-b", "feat", str(remote), cwd=tmp_path)
    run("git", "clone", str(remote), str(work), cwd=tmp_path)
    (work / ".github/workflows").mkdir(parents=True)
    (work / ".github/workflows/x.yml").write_text("a\n")
    run("git", "add", "."); run("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "base")
    run("git", "push", "origin", "HEAD:feat")
    comments = tmp_path / "comments.json"
    comments.write_text(json.dumps([[comment(fenced(GOOD))]]))
    env = {"PATH": os.environ["PATH"], "SENDER": "o", "OWNER": "o", "BRANCH": "feat", "HOME": str(tmp_path),
           "IMPLEMENTER_LOGIN": APP}
    done = subprocess.run(["python3", str(REPO / ".github/apply_workflow_patch.py"), str(comments), str(tmp_path / "r.md")],
                          cwd=work, env=env, capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    assert (work / ".github/workflows/x.yml").read_text() == "b\n"
    assert "applied the patch to .github/workflows/x.yml" in (tmp_path / "r.md").read_text()
    assert run("git", "log", "--oneline", "feat", cwd=remote).stdout.count("\n") == 2


def test_a_refusal_through_main_writes_the_reason_and_pushes_nothing(tmp_path: Path):
    comments = tmp_path / "comments.json"
    comments.write_text(json.dumps([[comment(fenced(GOOD))]]))
    env = {"PATH": os.environ["PATH"], "SENDER": "o-implementer[bot]", "OWNER": "o", "BRANCH": "feat"}
    done = subprocess.run(["python3", str(REPO / ".github/apply_workflow_patch.py"), str(comments), str(tmp_path / "r.md")],
                          cwd=tmp_path, env=env, capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    assert "only the repository owner's label applies a patch" in (tmp_path / "r.md").read_text()


def test_the_workflow_is_wired_to_the_owner_s_label_and_main_s_script():
    wf = yaml.safe_load((REPO / ".github/workflows/apply-workflow-patch.yml").read_text())
    assert wf[True]["pull_request_target"]["types"] == ["labeled"]
    job = wf["jobs"]["apply"]
    assert "github.event.sender.login == github.repository_owner" in job["if"]
    assert "'apply-workflow-patch'" in job["if"]
    assert job["steps"][0]["with"]["ref"] == "${{ github.event.repository.default_branch }}"
    assert any("../main/.github/apply_workflow_patch.py" in s.get("run", "") for s in job["steps"])
    assert "WORKFLOW_PUSH_TOKEN" in str(job["steps"][1]["with"]["token"])
    # the implementer app's login: the repository's variable, else the owner's shared pair (OWNER-implementer)
    assert job["env"]["IMPLEMENTER_LOGIN"] == (
        "${{ vars.IMPLEMENTER_APP_SLUG || format('{0}-implementer', github.repository_owner) }}[bot]")


def test_the_job_may_comment_on_the_pull_request_and_remove_the_label():
    """The last step's comment and label removal on a pull request need pull-requests: write (a 403 without it)."""
    job = yaml.safe_load((REPO / ".github/workflows/apply-workflow-patch.yml").read_text())["jobs"]["apply"]
    assert job["permissions"]["pull-requests"] == "write"
    assert job["permissions"]["contents"] == "read"


def scratch(tmp_path: Path, patch: str) -> tuple[Path, Path]:
    """A repository with a bare remote, the files .github/workflows/x.yml, AGENTS.md; the script's run on PATCH."""
    remote, work = tmp_path / "remote.git", tmp_path / "work"
    run = lambda *a, cwd=work: subprocess.run(a, cwd=cwd, check=True, capture_output=True, text=True)  # noqa: E731
    run("git", "init", "--bare", "-b", "feat", str(remote), cwd=tmp_path)
    run("git", "clone", str(remote), str(work), cwd=tmp_path)
    (work / ".github/workflows").mkdir(parents=True)
    (work / ".github/workflows/x.yml").write_text("a\n")
    (work / "AGENTS.md").write_text("rules\n")
    run("git", "add", ".")
    run("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "base")
    run("git", "push", "origin", "HEAD:feat")
    comments = tmp_path / "comments.json"
    comments.write_text(json.dumps([[comment(fenced(patch))]]))
    env = {"PATH": os.environ["PATH"], "SENDER": "o", "OWNER": "o", "BRANCH": "feat", "HOME": str(tmp_path),
           "IMPLEMENTER_LOGIN": APP}
    done = subprocess.run(["python3", str(REPO / ".github/apply_workflow_patch.py"), str(comments), str(tmp_path / "r.md")],
                          cwd=work, env=env, capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    return work, remote


def test_what_git_applies_is_checked_not_the_text_of_the_patch(tmp_path: Path):
    """A legacy `rename old` header and a patch with no a/ b/ prefix passed the check of the text."""
    legacy = ("diff --git a/.github/workflows/x.yml b/.github/workflows/x.yml\nsimilarity index 100%\n"
              "rename old AGENTS.md\nrename new .github/workflows/y.yml\n")
    assert awp.refusal(legacy) is None, "the text check alone passes it: the check of git's result is the guard"
    work, remote = scratch(tmp_path, legacy)
    assert "git would change AGENTS.md" in (tmp_path / "r.md").read_text()
    assert (work / "AGENTS.md").read_text() == "rules\n" and not (work / ".github/workflows/y.yml").exists()
    assert subprocess.run(["git", "log", "--oneline", "feat"], cwd=remote, capture_output=True, text=True, check=False).stdout.count("\n") == 1


def test_a_patch_with_no_prefix_is_not_written_one_directory_deeper(tmp_path: Path):
    bare = "--- /dev/null\n+++ .github/workflows/evil.yml\n@@ -0,0 +1 @@\n+a\n"
    assert awp.refusal(bare) is None
    work, _ = scratch(tmp_path, bare)
    assert "git would change workflows/evil.yml" in (tmp_path / "r.md").read_text()
    assert not (work / "workflows").exists()


FAKE_GH = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
cfg = json.load(open(os.environ["GH_CFG"]))
open(os.environ["GH_LOG"], "a").write(" ".join(args) + "\\n")
log = open(os.environ["GH_LOG"]).read().splitlines()
marks = [l for l in log if l.startswith("pr ready") and "--undo" not in l]
if args[:2] == ["pr", "view"]:
    print(cfg["draft"])
elif args[:2] == ["pr", "ready"] and cfg.get("ready_fails"):
    print("denied", file=sys.stderr); sys.exit(1)
elif args[:2] == ["run", "list"] and "claude-code-review.yml" in args:
    # the review run appears after the mark number `review_after` (0: never)
    if cfg.get("review_after") and len(marks) >= cfg["review_after"]:
        print(json.dumps([{"databaseId": 5, "createdAt": "2999-01-01T00:00:00Z"}]))
    else:
        print("[]")
elif args[:2] == ["run", "list"]:
    print(json.dumps([{"headSha": "abc1234ffff"}] if cfg.get("push_run", True) else []))
elif args[:2] == ["run", "view"]:
    print(json.dumps({"jobs": [{"name": "start", "conclusion": cfg.get("start", "success")}]}))
"""


def fake_gh(tmp_path: Path, draft: str = "true", **cfg: Any) -> dict[str, str]:
    """A `gh` on PATH that logs each call and answers from the config: the draft flag, a run of the push, and the review run."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "gh").write_text(FAKE_GH)
    (bin_dir / "gh").chmod(0o755)
    (tmp_path / "cfg.json").write_text(json.dumps({"draft": draft} | cfg))
    return {"PATH": f"{bin_dir}:{os.environ['PATH']}", "GH_LOG": str(tmp_path / "gh.log"), "GH_CFG": str(tmp_path / "cfg.json"),
            "PR": "7", "GITHUB_REPOSITORY": "o/r", "BRANCH": "feat", "READY_POLL_SECONDS": "0.01",
            "READY_PUSH_WAIT_SECONDS": "0.05", "READY_CHECK_SECONDS": "0.05"}


def ready_run(tmp_path: Path, env: dict[str, str], applied: bool = True) -> tuple[str, str]:
    result = tmp_path / "r.md"
    result.write_text("fallback\n")
    if applied:
        (tmp_path / "r.md.applied").write_text(".github/workflows/x.yml at abc1234\n")
    done = subprocess.run(["python3", str(REPO / ".github/apply_workflow_patch.py"), "ready", str(result)],
                          env=env, capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr
    log = tmp_path / "gh.log"
    return result.read_text(), log.read_text() if log.exists() else ""


def test_after_an_apply_a_draft_is_marked_ready_and_the_comment_says_so_when_the_review_run_exists(tmp_path: Path):
    text, log = ready_run(tmp_path, fake_gh(tmp_path, review_after=1))
    assert text == ("The label applied the patch to .github/workflows/x.yml at abc1234, and marked the pull request ready: "
                    "the review starts.\n")
    assert log.count("pr ready 7 -R o/r\n") == 1 and "--undo" not in log


def test_the_mark_waits_for_a_run_of_the_pushed_head(tmp_path: Path):
    """A mark seconds after a push can start no workflow: with no run of the push, the wait ends at its limit, then the mark."""
    text, log = ready_run(tmp_path, fake_gh(tmp_path, push_run=False, review_after=1))
    assert log.index("run list -R o/r --limit 50 --branch feat --json headSha") < log.index("pr ready 7")
    assert "the review starts" in text


def test_with_no_review_run_after_the_first_mark_the_pull_request_is_marked_once_more(tmp_path: Path):
    text, log = ready_run(tmp_path, fake_gh(tmp_path, review_after=2))
    assert log.count("pr ready 7 -R o/r --undo") == 1 and log.count("pr ready 7 -R o/r\n") == 2
    assert "the review starts" in text


def test_with_no_review_run_after_the_second_mark_the_comment_says_so_and_gives_the_owner_s_step(tmp_path: Path):
    text, log = ready_run(tmp_path, fake_gh(tmp_path))
    assert log.count("pr ready 7 -R o/r\n") == 2 and "the review starts" not in text
    assert "no review started" in text and "gh workflow run claude-code-review.yml -R o/r -f pr=7" in text


def test_a_review_run_whose_start_skipped_is_no_review(tmp_path: Path):
    text, _ = ready_run(tmp_path, fake_gh(tmp_path, review_after=1, start="skipped"))
    assert "no review started" in text


def test_a_refused_patch_marks_nothing_ready(tmp_path: Path):
    text, log = ready_run(tmp_path, fake_gh(tmp_path), applied=False)
    assert text == "fallback\n" and log == ""


def test_a_pull_request_that_is_not_a_draft_is_left_alone_and_the_owner_is_asked(tmp_path: Path):
    text, log = ready_run(tmp_path, fake_gh(tmp_path, "false"))
    assert "is not a draft" in text and "gh pr ready 7 --undo" in text and "pr ready" not in log


def test_a_failed_ready_mark_asks_the_owner_to_mark_it_ready(tmp_path: Path):
    text, _ = ready_run(tmp_path, fake_gh(tmp_path, ready_fails=True))
    assert "not marked ready (the ready mark failed: denied)" in text and "mark it ready to start the review" in text


def test_an_unreadable_pull_request_asks_the_owner_to_mark_it_ready(tmp_path: Path):
    env = fake_gh(tmp_path)
    (tmp_path / "bin" / "gh").unlink()  # no gh at all
    text, _ = ready_run(tmp_path, env)
    assert "not marked ready" in text and "mark it ready to start the review" in text


def test_the_apply_says_the_pull_request_is_not_marked_ready_until_the_ready_step_runs(tmp_path: Path):
    scratch(tmp_path, GOOD)
    assert "not marked ready" in (tmp_path / "r.md").read_text()
    assert (tmp_path / "r.md.applied").read_text().startswith(".github/workflows/x.yml at ")


def test_the_ready_step_holds_the_implementer_app_s_token_not_github_s():
    """`github.token` events start no workflow: a ready mark with it would start no review."""
    job = yaml.safe_load((REPO / ".github/workflows/apply-workflow-patch.yml").read_text())["jobs"]["apply"]
    ready = next(s for s in job["steps"] if "apply_workflow_patch.py ready" in s.get("run", ""))
    assert ready["env"]["GH_TOKEN"] == "${{ steps.token.outputs.token }}"
    token = next(s for s in job["steps"] if s.get("id") == "token")
    assert token["uses"].startswith("actions/create-github-app-token@")
    assert token["with"]["client-id"] == "${{ vars.IMPLEMENTER_APP_CLIENT_ID }}"
    assert token["with"]["private-key"] == "${{ secrets.IMPLEMENTER_APP_PRIVATE_KEY }}"
    assert token["with"]["permission-pull-requests"] == "write"
    assert token["with"]["permission-actions"] == "read", "the ready step reads the runs"
    assert job["timeout-minutes"] >= 8, "60 s for the push's runs, 100 s for each of two marks, and the other steps"
    assert "result.md.applied" in token["if"] and "result.md.applied" in ready["if"]
    assert job["steps"].index(ready) < len(job["steps"]) - 1, "the comment step comes after the ready step"
