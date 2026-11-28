"""The delivery workflow's own tools (AGENTS.md workflow block): `mise run start` (.workflow/start.py) removes only finished
worktrees and carries edits made on main; the edit guard (.agents/guards/edit_in_worktree.py, through the guards' contract) refuses the main checkout; the
push hook refuses a branch whose pull request is merged, and a push of HEAD while a tracked code file differs from it. git
runs for real on a local origin; gh and mise are stand-ins."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
START = REPO / ".workflow" / "start.py"
GUARD = REPO / ".agents" / "guards" / "guard.py"
PUSH = REPO / ".githooks" / "pre-push"

FAKE_GH = """#!/bin/sh
[ -n "$FAKE_GH_FAIL" ] && exit 1
case "$*" in
  *--head*) printf '%s\\n' "$FAKE_GH_MERGED" ;;
  *) printf '%s\\n' "${FAKE_PRS:-[]}" ;;
esac
"""
FAKE_MISE = """#!/bin/sh
echo "$*" >> "$FAKE_MISE_LOG"
"""


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    """git with an identity and no signing; gh and mise replaced; nothing else of the user's PATH but the system's."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name, text in (("gh", FAKE_GH), ("mise", FAKE_MISE)):
        (bin_ / name).write_text(text)
        (bin_ / name).chmod(0o755)
    git_dir = Path(subprocess.run(["sh", "-c", "command -v git"], capture_output=True, text=True, check=True).stdout.strip()).parent
    return {**os.environ, "PATH": f"{bin_}:{git_dir}:/usr/bin:/bin", "FAKE_MISE_LOG": str(tmp_path / "mise.log"),
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.org", "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.org", "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "commit.gpgsign",
            "GIT_CONFIG_VALUE_0": "false"}


def git(where: Path, env: dict[str, str], *args: str) -> str:
    return subprocess.run(["git", "-C", str(where), *args], env=env, capture_output=True, text=True, check=True).stdout.strip()


def start(where: Path, env: dict[str, str], *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(START), *args], cwd=where, env={**env, **extra}, capture_output=True, text=True, check=False)


@pytest.fixture
def main(tmp_path: Path, env: dict[str, str]) -> Path:
    """A main checkout on main, cloned from a bare origin holding one commit."""
    git(tmp_path, env, "init", "-q", "--bare", "-b", "main", "origin.git")
    git(tmp_path, env, "clone", "-q", str(tmp_path / "origin.git"), "main")
    checkout = tmp_path / "main"
    (checkout / "app.txt").write_text("one\n")
    (checkout / ".gitignore").write_text("/.claude/worktrees/\n")
    git(checkout, env, "add", ".")
    git(checkout, env, "commit", "-q", "-m", "first")
    git(checkout, env, "push", "-q", "origin", "main")
    return checkout


def merge_on_github(tmp_path: Path, env: dict[str, str], branch: str) -> None:
    """What `gh pr merge --merge` does: a merge commit on origin's main, made elsewhere."""
    other = tmp_path / "github"
    if not other.exists():
        git(tmp_path, env, "clone", "-q", str(tmp_path / "origin.git"), "github")
    git(other, env, "pull", "-q", "origin", "main")
    git(other, env, "fetch", "-q", "origin", branch)
    git(other, env, "merge", "-q", "--no-ff", "-m", f"Merge {branch}", "FETCH_HEAD")
    git(other, env, "push", "-q", "origin", "main")


def test_start_removes_only_finished_worktrees(tmp_path: Path, env: dict[str, str], main: Path) -> None:
    made = start(main, env, "abc-1-done")
    assert made.returncode == 0, made.stderr
    done = main / ".claude" / "worktrees" / "abc-1-done"
    assert made.stdout.strip().splitlines()[-1] == str(done.resolve()), "the last line is the new worktree"
    assert "run start:deps" in (tmp_path / "mise.log").read_text(), "its dependencies are installed"
    log = tmp_path / "mise.log"
    for _ in range(100):  # the warm-up is a detached process: start does not wait for it
        if "run start:warm" in log.read_text():
            break
        time.sleep(0.05)
    assert "run start:warm" in log.read_text(), "the web lint's caches are warmed in the background"
    (done / "app.txt").write_text("two\n")
    git(done, env, "commit", "-q", "-am", "the change")
    git(done, env, "push", "-q", "origin", "abc-1-done")
    merge_on_github(tmp_path, env, "abc-1-done")
    for name in ("abc-2-open", "abc-3-dirty", "abc-4-fresh"):
        assert start(main, env, name).returncode == 0
    (main / ".claude" / "worktrees" / "abc-3-dirty" / "notes.txt").write_text("not committed\n")
    prs = [{"headRefName": "abc-1-done", "state": "MERGED"}, {"headRefName": "abc-2-open", "state": "OPEN"},
           {"headRefName": "abc-3-dirty", "state": "MERGED"}]
    nxt = start(main, env, "abc-5-next", FAKE_PRS=json.dumps(prs))
    assert nxt.returncode == 0, nxt.stderr
    assert not done.exists() and "abc-1-done" not in git(main, env, "branch", "--list", "abc-1-done"), "merged and clean: removed"
    for line in ("removed abc-1-done (merged)", "kept abc-2-open: pull request open", "kept abc-3-dirty: merged, but files not committed",
                 "kept abc-4-fresh: no pull request"):
        assert line in nxt.stdout, line
    assert git(main, env, "rev-parse", "HEAD") == git(main, env, "rev-parse", "origin/main"), "the main checkout is pulled"
    assert (main / ".claude" / "worktrees" / "abc-5-next" / "app.txt").read_text() == "two\n", "a new branch starts from the newest main"
    assert "not swept" in start(main, env, "abc-6-later", FAKE_GH_FAIL="1").stdout, "without gh nothing is removed"
    assert start(main, env, "Bad Name").returncode == 1


def test_start_works_on_a_branch_that_exists(tmp_path: Path, env: dict[str, str], main: Path) -> None:
    """A restarted run, or one after the owner's answer, works on its pull request's branch (agents/implementer.md): start
    takes the branch with its commits, never a new branch from main beside it (R1-6)."""
    first = start(main, env, "abc-1-fix")
    assert first.returncode == 0, first.stderr
    work = main / ".claude" / "worktrees" / "abc-1-fix"
    (work / "app.txt").write_text("the pull request's commit\n")
    git(work, env, "commit", "-q", "-am", "the fix")
    git(work, env, "push", "-q", "-u", "origin", "abc-1-fix")
    git(main, env, "worktree", "remove", str(work))
    again = start(main, env, "abc-1-fix")  # the local branch and origin's
    assert again.returncode == 0, again.stderr
    assert "branch abc-1-fix: exists, up to origin/abc-1-fix" in again.stdout
    assert (work / "app.txt").read_text() == "the pull request's commit\n"
    git(main, env, "worktree", "remove", str(work))
    git(main, env, "branch", "-D", "abc-1-fix")
    elsewhere = start(main, env, "abc-1-fix")  # another machine: origin's branch only
    assert elsewhere.returncode == 0, elsewhere.stderr
    assert "branch abc-1-fix: exists, from origin/abc-1-fix" in elsewhere.stdout
    assert (work / "app.txt").read_text() == "the pull request's commit\n"
    assert git(work, env, "rev-parse", "--abbrev-ref", "@{upstream}") == "origin/abc-1-fix", "a push goes to the pull request"
    git(work, env, "commit", "-q", "--allow-empty", "-m", "local only")
    git(main, env, "worktree", "remove", str(work))
    failed = start(main, env, "abc-1-fix", "--carry", "missing.txt")  # the worktree is made, then the move fails (R1-1)
    assert failed.returncode == 1 and "not an untracked file" in failed.stderr
    assert not work.exists() and git(main, env, "log", "-1", "--format=%s", "abc-1-fix") == "local only", "the branch keeps its commits"
    other = tmp_path / "github"
    git(tmp_path, env, "clone", "-q", "-b", "abc-1-fix", str(tmp_path / "origin.git"), "github")
    git(other, env, "commit", "-q", "--allow-empty", "-m", "pushed elsewhere")
    git(other, env, "push", "-q", "origin", "abc-1-fix")
    diverged = start(main, env, "abc-1-fix", "--carry")
    assert diverged.returncode == 1 and "have diverged" in diverged.stderr
    assert not work.exists() and git(main, env, "log", "-1", "--format=%s", "abc-1-fix") == "local only", "the branch keeps its commits"
    assert "new, from origin/main" in start(main, env, "abc-2-other").stdout


def test_start_from_a_blockers_branch_makes_a_draft_stack(tmp_path: Path, env: dict[str, str], main: Path) -> None:
    """`--from BRANCH` starts a new branch from origin/BRANCH (a draft stack on its blocker's branch), which it
    never tracks; a branch that exists keeps its own commits, and a blocker that origin does not have is refused."""
    blocker = start(main, env, "abc-1-blocker")
    assert blocker.returncode == 0, blocker.stderr
    work = main / ".claude" / "worktrees" / "abc-1-blocker"
    (work / "app.txt").write_text("the blocker's commit\n")
    git(work, env, "commit", "-q", "-am", "the blocker")
    missing = start(main, env, "abc-2-stacked", "--from", "abc-1-blocker")  # not pushed yet
    assert missing.returncode == 1 and "origin has no such branch" in missing.stderr
    assert not (main / ".claude" / "worktrees" / "abc-2-stacked").exists()
    git(work, env, "push", "-q", "-u", "origin", "abc-1-blocker")
    stacked = start(main, env, "abc-2-stacked", "--from", "abc-1-blocker")
    assert stacked.returncode == 0, stacked.stderr
    assert "branch abc-2-stacked: new, from origin/abc-1-blocker" in stacked.stdout
    there = main / ".claude" / "worktrees" / "abc-2-stacked"
    assert (there / "app.txt").read_text() == "the blocker's commit\n"
    upstream = subprocess.run(["git", "-C", str(there), "rev-parse", "--abbrev-ref", "@{upstream}"], env=env,
                              capture_output=True, text=True, check=False)
    assert upstream.returncode != 0, "a plain push never reaches the blocker's branch"
    git(main, env, "worktree", "remove", str(there))
    again = start(main, env, "abc-2-stacked", "--from", "main")  # the branch exists: --from changes nothing
    assert "branch abc-2-stacked: exists" in again.stdout
    assert (there / "app.txt").read_text() == "the blocker's commit\n"
    assert start(main, env, "abc-3-x", "--from", "Bad Name").returncode == 1


def test_start_carries_edits_made_on_main(env: dict[str, str], main: Path) -> None:
    (main / "app.txt").write_text("edited on main\n")
    (main / "new.txt").write_text("a new file\n")
    refused = start(main, env, "abc-7-wrong", "--carry", "app.txt")
    assert refused.returncode == 1 and "not an untracked file" in refused.stderr
    assert not (main / ".claude" / "worktrees" / "abc-7-wrong").exists() and (main / "app.txt").read_text() == "edited on main\n"
    assert git(main, env, "branch", "--list", "abc-7-wrong") == "", "a branch made for the failed move goes with it"
    moved = start(main, env, "abc-8-idea", "--carry", "new.txt")
    assert moved.returncode == 0, moved.stderr
    idea = main / ".claude" / "worktrees" / "abc-8-idea"
    assert (idea / "app.txt").read_text() == "edited on main\n" and (idea / "new.txt").read_text() == "a new file\n"
    assert git(main, env, "status", "--porcelain") == "", "the main checkout is back to its last commit"
    assert "carried: app.txt, new.txt" in moved.stdout


def hook(file_path: Path) -> int:
    """2 when the edit guard refuses the change, 0 when it allows it (the numbers of Claude Code's hook, as before the adapter)."""
    action = json.dumps({"action": "edit", "path": str(file_path), "cwd": "/"})
    done = subprocess.run([sys.executable, str(GUARD)], input=action, capture_output=True, text=True, check=True)
    return 0 if json.loads(done.stdout)["allow"] else 2


def test_the_edit_hook_refuses_the_main_checkout_only(tmp_path: Path, env: dict[str, str], main: Path) -> None:
    assert hook(main / "app.txt") == 0, "a repository not on this workflow is left alone"
    git(main, env, "config", "core.hooksPath", ".githooks")
    (main / ".gitignore").write_text("/.claude/worktrees/\n/.env\n")
    assert hook(main / "app.txt") == 2 and hook(main / "src" / "new.py") == 2, "a change to the code, a new file too"
    assert hook(main / ".env") == 0, "an ignored file is local settings"
    assert start(main, env, "abc-9-work").returncode == 0
    assert hook(main / ".claude" / "worktrees" / "abc-9-work" / "app.txt") == 0, "a worktree is where changes go"
    assert hook(tmp_path / "outside.md") == 0, "outside any repository"


def test_the_push_hook_refuses_a_merged_branch(env: dict[str, str], pushed: Path) -> None:
    def push(refs: str, merged: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["sh", str(PUSH)], input=refs, cwd=pushed, env={**env, "FAKE_GH_MERGED": merged}, capture_output=True,
                              text=True, check=False)

    refs = f"refs/heads/abc-9-work {git(pushed, env, 'rev-parse', 'HEAD')} refs/heads/abc-9-work def456\n"
    refused = push(refs, "7")
    assert refused.returncode == 1 and "is merged" in refused.stdout
    assert push(refs, "").returncode == 0, "a branch not merged goes on to the checks (here mise is a stand-in)"
    deleting = "refs/heads/abc-9-work " + "0" * 40 + " refs/heads/abc-9-work def456\n"
    assert push(deleting, "7").returncode == 0, "deleting a merged branch is allowed"


@pytest.fixture
def pushed(env: dict[str, str], main: Path) -> Path:
    """A branch with one commit of code and docs, as a push hook sees it: the classifier at its place, an origin to compare to."""
    git(main, env, "switch", "-q", "-c", "abc-9-work")
    (main / ".github").mkdir()
    # a fake of the product's docs-only check: the hook only needs its answer (the script's own tests: tests/test_product_ci.py)
    (main / ".github" / "code-changed.sh").write_text("grep -qv '[.]md\"\\{0,1\\}$' && echo true || echo false\n")
    (main / "app.py").write_text("x = 1\n")
    (main / "README.md").write_text("Read me.\n")
    git(main, env, "add", ".")
    git(main, env, "commit", "-q", "-m", "work")
    return main


def push_hook(where: Path, env: dict[str, str], local_sha: str) -> subprocess.CompletedProcess[str]:
    """The hook as git runs it for `git push origin abc-9-work` of a new branch (no pull request, so none merged)."""
    refs = f"refs/heads/abc-9-work {local_sha} refs/heads/abc-9-work {'0' * 40}\n"
    return subprocess.run(["sh", str(PUSH)], input=refs, cwd=where, env={**env, "FAKE_GH_MERGED": ""}, capture_output=True,
                          text=True, check=False)


def checked(tmp_path: Path) -> bool:
    log = tmp_path / "mise.log"
    return log.exists() and "check" in log.read_text()


def test_the_push_hook_refuses_an_unstaged_code_edit(tmp_path: Path, env: dict[str, str], pushed: Path) -> None:
    (pushed / "app.py").write_text("x = 2\n")
    refused = push_hook(pushed, env, git(pushed, env, "rev-parse", "HEAD"))
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert "  app.py\n" in refused.stdout and "commit them, or undo the edits" in refused.stdout
    assert not checked(tmp_path), "the checks would read the edit, not the commit: they do not run"


def test_the_push_hook_refuses_a_staged_code_edit(tmp_path: Path, env: dict[str, str], pushed: Path) -> None:
    (pushed / "app.py").write_text("x = 2\n")
    git(pushed, env, "add", "app.py")
    refused = push_hook(pushed, env, git(pushed, env, "rev-parse", "HEAD"))
    assert refused.returncode == 1 and "  app.py\n" in refused.stdout
    assert not checked(tmp_path)


def test_the_push_hook_ignores_untracked_files_and_docs(tmp_path: Path, env: dict[str, str], pushed: Path) -> None:
    head = git(pushed, env, "rev-parse", "HEAD")
    (pushed / "left.py").write_text("by a tool\n")
    untracked = push_hook(pushed, env, head)
    assert untracked.returncode == 0 and checked(tmp_path), "an untracked file is not in the push: the checks run"
    (pushed / "README.md").write_text("Read me again.\n")
    docs = push_hook(pushed, env, head)
    assert docs.returncode == 0 and "differ" not in docs.stdout, "a docs edit changes no check"


def test_the_push_hook_guards_only_a_push_of_head(tmp_path: Path, env: dict[str, str], pushed: Path) -> None:
    other = git(pushed, env, "rev-parse", "HEAD")
    (pushed / "app.py").write_text("x = 3\n")
    git(pushed, env, "commit", "-q", "-am", "more")
    (pushed / "app.py").write_text("x = 4\n")
    passed = push_hook(pushed, env, other)
    assert passed.returncode == 0 and checked(tmp_path), "another commit than HEAD: the checks run as before"


def test_the_push_hook_guards_head_after_another_code_ref(tmp_path: Path, env: dict[str, str], pushed: Path) -> None:
    """The hook once ran the checks and ended at the first code ref: the guard then never saw a later ref that is HEAD."""
    other = git(pushed, env, "rev-parse", "HEAD")
    (pushed / "app.py").write_text("x = 3\n")
    git(pushed, env, "commit", "-q", "-am", "more")
    (pushed / "app.py").write_text("x = 4\n")
    zero = "0" * 40
    refs = f"refs/heads/abc-8-old {other} refs/heads/abc-8-old {zero}\nrefs/heads/abc-9-work {git(pushed, env, 'rev-parse', 'HEAD')} refs/heads/abc-9-work {zero}\n"
    refused = subprocess.run(["sh", str(PUSH)], input=refs, cwd=pushed, env={**env, "FAKE_GH_MERGED": ""}, capture_output=True,
                             text=True, check=False)
    assert refused.returncode == 1 and "  app.py\n" in refused.stdout, refused.stdout + refused.stderr
    assert not checked(tmp_path)


def test_the_push_hook_reads_a_quoted_docs_name_as_docs(tmp_path: Path, env: dict[str, str], pushed: Path) -> None:
    """git quotes a name with a non-ASCII byte ("caf\\303\\251.md"): the classifier once read it as code, and the guard
    refused a push for a docs edit."""
    (pushed / "café.md").write_text("Un café.\n")
    git(pushed, env, "add", "café.md")
    git(pushed, env, "commit", "-q", "-m", "docs")
    (pushed / "café.md").write_text("Deux cafés.\n")
    passed = push_hook(pushed, env, git(pushed, env, "rev-parse", "HEAD"))
    assert passed.returncode == 0 and "differ" not in passed.stdout, passed.stdout + passed.stderr


@pytest.mark.parametrize("name", ["fix-the-thing", "fix-abc-12", "abc-x", "abc12-x", "try", "a-1-x", "abcdefg-9"])
def test_start_refuses_a_name_without_an_issue_key(env: dict[str, str], main: Path, name: str) -> None:
    """A pull request's branch `fix-the-thing` had no issue. The name starts with the key (`abc-123-short-name`), or
    `try-` for a measurement; the message says to file the issue first. Nothing is made."""
    refused = start(main, env, name)
    assert refused.returncode == 1 and "has no issue key" in refused.stderr and "File it first" in refused.stderr
    assert not (main / ".claude" / "worktrees" / name).exists() and git(main, env, "branch", "--list", name) == ""


def test_start_takes_a_keyed_name_and_a_measurement(env: dict[str, str], main: Path) -> None:
    for name in ("abc-999-x", "abc-999", "xyz-4-x", "try-x"):
        made = start(main, env, name)
        assert made.returncode == 0, (name, made.stderr)
        assert made.stdout.strip().splitlines()[-1] == str((main / ".claude" / "worktrees" / name).resolve())
    refused = start(main, env, "stacked", "--from", "abc-999-x")
    assert refused.returncode == 1 and "has no issue key" in refused.stderr, "a draft stack keeps the rule"


@pytest.mark.parametrize(("branch", "passes"), [("abc-9-work", True), ("abc-999", True), ("try-x", True), ("ABC-12-upper", True),
                                                ("fix-the-thing", False), ("abcdefg-9", False), ("fix-bug-2", False), ("abc-", False), ("a-1-x", False), ("xyz-4-other-team", True), ("work", False)])
def test_the_push_hook_refuses_a_branch_without_an_issue_key(env: dict[str, str], pushed: Path, branch: str, passes: bool) -> None:
    """A branch made without `mise run start` is caught at the push. Deleting such a branch is allowed."""
    head = git(pushed, env, "rev-parse", "HEAD")
    refs = f"refs/heads/{branch} {head} refs/heads/{branch} {'0' * 40}\n"
    done = subprocess.run(["sh", str(PUSH)], input=refs, cwd=pushed, env={**env, "FAKE_GH_MERGED": ""}, capture_output=True,
                          text=True, check=False)
    assert (done.returncode == 0) == passes, done.stdout + done.stderr
    assert ("has no issue key" in done.stdout) == (not passes)
    deleting = f"refs/heads/{branch} {'0' * 40} refs/heads/{branch} {head}\n"
    assert subprocess.run(["sh", str(PUSH)], input=deleting, cwd=pushed, env={**env, "FAKE_GH_MERGED": ""}, capture_output=True,
                          text=True, check=False).returncode == 0, "deleting a branch is allowed"


# ---- OWL-56: a pure bump of the loop's files needs no issue key, and nothing else without a key does

BUMP = "loop-bump-0123456789ab"


@pytest.mark.parametrize(("name", "passes"), [(BUMP, True), ("loop-bump-0123456789a", False), ("loop-bump-0123456789abc", False),
                                              ("loop-bump-", False), ("loop-bump", False)])
def test_start_takes_only_the_branch_of_a_pure_bump_without_a_key(env: dict[str, str], main: Path, name: str, passes: bool) -> None:
    made = start(main, env, name)
    assert (made.returncode == 0) == passes, made.stderr
    if not passes:
        assert "has no issue key" in made.stderr and "loop:bump" in made.stderr


def bump_branch(env: dict[str, str], main: Path, extra: dict[str, str]) -> str:
    """A branch `loop-bump-...` from main: the pin, the loop's list and the files it names, plus `extra` files; the classifier of
    the hook is left untracked, as a tool leaves it. Returns the commit."""
    git(main, env, "switch", "-q", "-c", BUMP)
    (main / ".github").mkdir()
    (main / ".github" / "code-changed.sh").write_text("grep -qv '[.]md\"\\{0,1\\}$' && echo true || echo false\n")
    files = {".workflow/loop.toml": 'repository = "o/l"\ncommit = "' + "a" * 40 + '"\n', ".workflow/loop-files.txt": "agents/\n.workflow/\n",
             ".workflow/loop_sync.py": (REPO / ".workflow" / "loop_sync.py").read_text(), "agents/a.md": "a\n", **extra}
    for path, text in files.items():
        (main / path).parent.mkdir(parents=True, exist_ok=True)
        (main / path).write_text(text)
    git(main, env, "add", ".workflow", "agents", *extra)
    git(main, env, "commit", "-q", "-m", "bump")
    return git(main, env, "rev-parse", "HEAD")


def push_branch(where: Path, env: dict[str, str], branch: str, sha: str) -> subprocess.CompletedProcess[str]:
    refs = f"refs/heads/{branch} {sha} refs/heads/{branch} {'0' * 40}\n"
    return subprocess.run(["sh", str(PUSH)], input=refs, cwd=where, env={**env, "FAKE_GH_MERGED": ""}, capture_output=True, text=True,
                          check=False)


def test_the_push_hook_takes_a_pure_bump_and_refuses_a_bump_that_changes_a_product_file(env: dict[str, str], main: Path) -> None:
    sha = bump_branch(env, main, {})
    done = push_branch(main, env, BUMP, sha)
    assert done.returncode == 0, done.stdout + done.stderr
    (main / "src").mkdir()
    (main / "src" / "app.py").write_text("x = 1\n")
    git(main, env, "add", "src")
    git(main, env, "commit", "-q", "-m", "a product change")
    refused = push_branch(main, env, BUMP, git(main, env, "rev-parse", "HEAD"))
    assert refused.returncode == 1 and "changes more than a pure bump" in refused.stdout and "src/app.py" in refused.stdout, refused.stdout


@pytest.mark.parametrize("deletes", [True, False])
def test_the_push_hook_takes_a_file_of_the_old_list_only_when_the_bump_deletes_it(env: dict[str, str], main: Path, deletes: bool) -> None:
    """R1-2 of PR #17: main's list names `gone/`, the bump's list does not. Deleting `gone/x.md` is a bump; editing it is not."""
    (main / ".workflow").mkdir()
    (main / "gone").mkdir()
    (main / ".workflow" / "loop-files.txt").write_text("agents/\n.workflow/\ngone/\n")
    (main / "gone" / "x.md").write_text("x\n")
    git(main, env, "add", ".workflow", "gone")
    git(main, env, "commit", "-q", "-m", "the old list")
    git(main, env, "push", "-q", "origin", "main")
    sha = bump_branch(env, main, {} if deletes else {"gone/x.md": "changed\n"})
    if deletes:
        git(main, env, "rm", "-q", "gone/x.md")
        git(main, env, "commit", "-q", "-m", "the file left the loop")
        sha = git(main, env, "rev-parse", "HEAD")
    done = push_branch(main, env, BUMP, sha)
    assert (done.returncode == 0) == deletes, done.stdout + done.stderr
    assert deletes or "gone/x.md" in done.stdout


def test_the_push_hook_refuses_a_bump_branch_without_the_loops_script_or_list(env: dict[str, str], main: Path) -> None:
    git(main, env, "switch", "-q", "-c", BUMP)
    (main / "app.txt").write_text("two\n")
    git(main, env, "commit", "-qam", "no script")
    refused = push_branch(main, env, BUMP, git(main, env, "rev-parse", "HEAD"))
    assert refused.returncode == 1 and "has no issue key" in refused.stdout, "no .workflow/loop_sync.py: no bump"
