"""`mise run loop:bump` and `mise run loop:check` (.workflow/loop_sync.py): the loop's files are a copy of the loop's
repository at a pinned commit. A fake loop repository (a local folder, `LOOP_REPO_URL`) and a fake product, both in
tmp_path; the script runs with the Mac's `/usr/bin/python3` in the task, so it runs here with the same flags."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / ".workflow" / "loop_sync.py"
LIST = ".workflow/loop-files.txt"
PIN = ".workflow/loop.toml"


def env(tmp_path: Path) -> dict[str, str]:
    return {**os.environ, "HOME": str(tmp_path), "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.org", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.org",
            "LOOP_READ_TOKEN": "unused-in-a-local-folder"}


def git(where: Path, environ: dict[str, str], *args: str) -> str:
    return subprocess.run(["git", "-C", str(where), *args], env=environ, capture_output=True, text=True,
                          check=True).stdout.strip()


def put(root: Path, files: dict[str, str]) -> None:
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text)


def commit(root: Path, environ: dict[str, str], message: str) -> str:
    git(root, environ, "add", "-A")
    git(root, environ, "commit", "-q", "-m", message)
    return git(root, environ, "rev-parse", "HEAD")


def write_pin(product: Path, sha: str) -> None:
    put(product, {PIN: f'# the pin\nrepository = "owner/loop"\ncommit = "{sha}"\n'})


def sync(product: Path, environ: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-I", str(SCRIPT), *args], cwd=product, env=environ, capture_output=True,
                          text=True, check=False)


@pytest.fixture
def world(tmp_path: Path) -> tuple[Path, dict[str, str], list[str]]:
    """The loop's repository with two commits on main (the second changes a.md, drops b.md from the list, deletes d.md and adds
    c.md) and one on a side branch; a product on a branch whose copy is the first commit and whose pin names it.
    Returns the product, the environment and the main commits."""
    environ = env(tmp_path)
    origin = tmp_path / "origin"
    origin.mkdir()
    git(origin, environ, "init", "-q", "-b", "main")
    put(origin, {LIST: ".workflow/loop-files.txt\nagents/\n", "agents/a.md": "a1\n", "agents/b.md": "b1\n", "agents/d.md": "d1\n"})
    one = commit(origin, environ, "one")
    put(origin, {LIST: ".workflow/loop-files.txt\nagents/\n!agents/b.md\n", "agents/a.md": "a2\n", "agents/c.md": "c2\n"})
    (origin / "agents" / "d.md").unlink()
    two = commit(origin, environ, "two")
    git(origin, environ, "switch", "-q", "-c", "side", one)
    put(origin, {"agents/side.md": "side\n"})
    commit(origin, environ, "side")
    git(origin, environ, "switch", "-q", "main")
    environ.update(LOOP_REPO_URL=str(origin), LOOP_CACHE=str(tmp_path / "cache.git"))
    product = tmp_path / "product"
    product.mkdir()
    git(product, environ, "init", "-q", "-b", "main")
    put(product, {LIST: ".workflow/loop-files.txt\nagents/\n", "agents/a.md": "a1\n", "agents/b.md": "b1\n", "agents/d.md": "d1\n", "agents/own.md": "the product's\n"})
    write_pin(product, one)
    commit(product, environ, "the copy")
    git(product, environ, "switch", "-q", "-c", "abc-1-bump")
    return product, environ, [one, two]


def test_check_passes_on_the_copy_and_ignores_a_file_the_list_does_not_name(world) -> None:
    product, environ, shas = world
    run = sync(product, environ, "check")
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == f"loop: 0 files differ from {shas[0][:12]}"


def test_check_names_a_hand_edited_file_and_a_missing_one_and_exits_1(world) -> None:
    product, environ, shas = world
    (product / "agents" / "a.md").write_text("a1 edited by hand\n")
    (product / "agents" / "b.md").unlink()
    run = sync(product, environ, "check")
    assert run.returncode == 1
    assert f"agents/a.md: differs from {shas[0][:12]}" in run.stdout
    assert "agents/b.md: missing" in run.stdout
    assert f"loop: 2 files differ from {shas[0][:12]}" in run.stdout
    assert "pull request in the loop's repository" in run.stderr and "loop:bump" in run.stderr


def test_check_sees_a_changed_executable_bit(world) -> None:
    product, environ, _ = world
    (product / "agents" / "a.md").chmod(0o755)
    run = sync(product, environ, "check")
    assert run.returncode == 1 and "agents/a.md" in run.stdout


def test_check_refuses_a_pin_that_is_not_a_full_hash(world) -> None:
    product, environ, _ = world
    write_pin(product, "abc123")
    run = sync(product, environ, "check")
    assert run.returncode == 1 and "40 hex digits" in run.stderr


def test_check_says_when_the_pin_is_not_in_the_loops_repository(world) -> None:
    product, environ, _ = world
    write_pin(product, "0" * 40)
    run = sync(product, environ, "check")
    assert run.returncode == 1 and "not in the loop's repository" in run.stderr


def test_bump_copies_the_new_files_removes_the_ones_that_left_the_list_and_writes_the_pin(world) -> None:
    product, environ, shas = world
    run = sync(product, environ, "bump", shas[1][:8])
    assert run.returncode == 0, run.stderr
    assert (product / "agents" / "a.md").read_text() == "a2\n"
    assert (product / "agents" / "c.md").read_text() == "c2\n"
    assert not (product / "agents" / "d.md").exists(), "a file that the loop's repository dropped is removed"
    assert (product / "agents" / "b.md").read_text() == "b1\n", "a file that only left the list stays: it may be the product's own"
    assert "kept agents/b.md" in run.stdout
    assert (product / "agents" / "own.md").exists(), "a file that no list names stays"
    assert (product / LIST).read_text() == ".workflow/loop-files.txt\nagents/\n!agents/b.md\n"
    assert f'commit = "{shas[1]}"' in (product / PIN).read_text() and "# the pin" in (product / PIN).read_text()
    assert "two" in run.stdout and "+a2" in run.stdout, "the commits and the diff"
    assert sync(product, environ, "check").returncode == 0


def test_bump_to_the_pin_that_stands_changes_no_file(world) -> None:
    product, environ, shas = world
    run = sync(product, environ, "bump", shas[0])
    assert run.returncode == 0, run.stderr
    assert git(product, environ, "status", "--porcelain") == ""


def test_bump_refuses_a_commit_that_is_not_on_the_loops_main(world) -> None:
    product, environ, shas = world
    side = git(Path(environ["LOOP_REPO_URL"]), environ, "rev-parse", "side")
    run = sync(product, environ, "bump", side)
    assert run.returncode == 1 and "not on the loop's main" in run.stderr
    assert f'commit = "{shas[0]}"' in (product / PIN).read_text()


def test_bump_in_a_worktree_refuses_a_detached_head(world) -> None:
    product, environ, shas = world
    git(product, environ, "switch", "-q", "--detach")
    run = sync(product, environ, "bump", shas[1])
    assert run.returncode == 1 and "a branch of a worktree" in run.stderr


def test_a_wrong_command_prints_the_usage(world) -> None:
    product, environ, _ = world
    assert sync(product, environ).returncode == 2
    assert sync(product, environ, "bump", "--now").returncode == 2
    assert sync(product, environ, "bump", "a", "b").returncode == 2


def test_bump_to_an_older_commit_says_so_and_moves_the_pin(world) -> None:
    product, environ, shas = world
    write_pin(product, shas[1])
    sync(product, environ, "bump", shas[1])
    run = sync(product, environ, "bump", shas[0])
    assert run.returncode == 0, run.stderr
    assert "does not follow the old pin" in run.stdout
    assert f'commit = "{shas[0]}"' in (product / PIN).read_text()


def load_script():
    import importlib.util
    spec = importlib.util.spec_from_file_location("loop_sync", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_token_comes_from_the_environment_else_from_gh_else_nothing(monkeypatch, tmp_path: Path) -> None:
    module = load_script()
    monkeypatch.setenv("LOOP_READ_TOKEN", "from-env")
    assert module.token() == "from-env"
    monkeypatch.delenv("LOOP_READ_TOKEN")
    monkeypatch.setenv("PATH", str(tmp_path))  # no gh on the path
    assert module.token() == ""


# ---- the token goes to git in the environment, never in an argument (OWL-56 item 7)

LOGGING_GIT = """#!/bin/sh
{ printf 'ARGS'; for a in "$@"; do printf ' [%s]' "$a"; done; printf '\\n'; env | grep '^GIT_CONFIG_' | sed 's/^/ENV /'; } >> "$FAKE_LOG"
exec "$REAL_GIT" "$@"
"""


def test_the_token_reaches_git_in_the_environment_and_in_no_argument(world, tmp_path: Path) -> None:
    import base64
    import shutil
    product, environ, _ = world
    fake = tmp_path / "fakebin"
    fake.mkdir()
    (fake / "git").write_text(LOGGING_GIT)
    (fake / "git").chmod(0o755)
    log = tmp_path / "git.log"
    secret = "s3cret-token-value"
    environ.update(PATH=f"{fake}:{environ['PATH']}", FAKE_LOG=str(log), REAL_GIT=shutil.which("git") or "git", LOOP_READ_TOKEN=secret)
    run = sync(product, environ, "check")
    assert run.returncode == 0, run.stderr
    text = log.read_text()
    basic = base64.b64encode(f"x-access-token:{secret}".encode()).decode()
    args = [line for line in text.splitlines() if line.startswith("ARGS")]
    assert args, "git ran"
    assert not any(secret in line or basic in line or "extraHeader" in line for line in args), args
    assert "ENV GIT_CONFIG_COUNT=1" in text and "ENV GIT_CONFIG_KEY_0=http.extraHeader" in text
    assert f"ENV GIT_CONFIG_VALUE_0=Authorization: Basic {basic}" in text


# ---- the one command: `loop:bump [REF]` on main makes the whole pull request

FAKE_GH = """#!/bin/sh
echo "gh $*" >> "$FAKE_LOG"
case "$FAKE_GH_FAIL" in 1) echo "gh: boom" >&2; exit 1;; esac
[ "$1 $2" = "pr create" ] && echo "https://github.com/owner/product/pull/9"
exit 0
"""


@pytest.fixture
def on_main(world, tmp_path: Path) -> tuple[Path, dict[str, str], list[str], Path]:
    """The product back on main, with a bare origin that holds main, and a fake `gh` that logs its calls."""
    product, environ, shas = world
    git(product, environ, "switch", "-q", "main")
    bare = tmp_path / "product.git"
    git(tmp_path, environ, "init", "-q", "--bare", str(bare))
    git(product, environ, "remote", "add", "origin", str(bare))
    git(product, environ, "push", "-q", "-u", "origin", "main")
    fake = tmp_path / "fakebin"
    fake.mkdir()
    (fake / "gh").write_text(FAKE_GH)
    (fake / "gh").chmod(0o755)
    log = tmp_path / "gh.log"
    log.write_text("")
    environ.update(PATH=f"{fake}:{environ['PATH']}", FAKE_LOG=str(log), FAKE_GH_FAIL="")
    return product, environ, shas, log


def test_bump_on_main_makes_the_branch_commits_pushes_and_opens_the_pull_request(on_main) -> None:
    product, environ, (one, two), log = on_main
    run = sync(product, environ, "bump")
    assert run.returncode == 0, run.stderr
    name = f"loop-bump-{two[:12]}"
    tree = product / ".claude" / "worktrees" / name
    assert run.stdout.strip().splitlines()[-1] == "https://github.com/owner/product/pull/9"
    assert git(tree, environ, "branch", "--show-current") == name
    assert f'commit = "{two}"' in (tree / PIN).read_text()
    assert (tree / "agents" / "a.md").read_text() == "a2\n" and (tree / "agents" / "c.md").read_text() == "c2\n"
    assert not (tree / "agents" / "d.md").exists()
    assert (tree / "agents" / "b.md").exists(), "b.md left the list but the loop still holds it: kept"
    assert git(product, environ, "status", "--short", "--", ".", ":!.claude") == "", "the main checkout is untouched"
    assert git(product, environ, "rev-parse", f"origin/{name}") == git(tree, environ, "rev-parse", "HEAD"), "pushed"
    body = git(tree, environ, "log", "-1", "--format=%B")
    assert f"Bump the loop to {two[:12]}" in body and f"{one[:12]}" in body and "two" in body, body
    assert "- .workflow/loop.toml" in body and "- agents/a.md" in body and "- agents/d.md" in body
    assert "Kept, for this product to remove or own:\n- agents/b.md" in body
    [call] = [c for c in log.read_text().splitlines() if c.startswith("gh pr create")]
    assert f"--head {name}" in call and "--draft" not in call and f"--title Bump the loop to {two[:12]}" in call


def test_bump_with_the_pin_at_ref_s_commit_does_nothing(on_main) -> None:
    product, environ, (_, two), log = on_main
    write_pin(product, two)
    commit(product, environ, "the pin moves")
    git(product, environ, "push", "-q")
    run = sync(product, environ, "bump", "main")
    assert run.returncode == 0 and run.stdout.strip() == f"the pin stands at {two[:12]}: nothing to do", run.stdout + run.stderr
    assert not (product / ".claude").exists() and "gh" not in log.read_text()
    assert git(product, environ, "branch", "--list", "loop-bump-*") == ""


def test_a_failed_push_prints_what_was_done_and_the_next_step_and_keeps_the_worktree(on_main, tmp_path: Path) -> None:
    product, environ, (_, two), log = on_main
    git(product, environ, "remote", "set-url", "--push", "origin", str(tmp_path / "nowhere.git"))
    run = sync(product, environ, "bump")
    tree = product / ".claude" / "worktrees" / f"loop-bump-{two[:12]}"
    assert run.returncode == 1
    assert "done so far" in run.stderr and "the commit" in run.stderr and "the push failed" in run.stderr
    assert f"git -C {tree} push -u origin loop-bump-{two[:12]}" in run.stderr
    assert tree.is_dir() and git(tree, environ, "log", "-1", "--format=%s").startswith("Bump the loop to")
    assert "gh pr create" not in log.read_text()


def test_a_failed_pull_request_names_the_next_step(on_main) -> None:
    product, environ, _, _ = on_main
    environ["FAKE_GH_FAIL"] = "1"
    run = sync(product, environ, "bump")
    assert run.returncode == 1 and "the push" in run.stderr and "the pull request failed" in run.stderr and "gh pr create --head" in run.stderr


def test_on_a_branch_bump_still_needs_a_commit(world) -> None:
    product, environ, _ = world
    run = sync(product, environ, "bump")
    assert run.returncode == 1 and "name the commit" in run.stderr


# ---- `pure`: a diff of the pin and listed files only

def test_pure_names_each_path_that_is_not_the_pin_or_a_listed_file(tmp_path: Path) -> None:
    listing = tmp_path / "list.txt"
    listing.write_text("# list\n.workflow/\nagents/\n!agents/own.md\n.github/*.py\ntests/test_x.py\n")
    paths = [PIN, ".workflow/start.py", "agents/a.md", ".github/linear.py", "tests/test_x.py"]
    ok = subprocess.run([sys.executable, "-I", str(SCRIPT), "pure", str(listing)], input="\n".join(paths) + "\n", capture_output=True, text=True, check=False)
    assert ok.returncode == 0 and ok.stdout == ""
    other = ["agents/own.md", "src/app.py", ".github/workflows/gates.yml", ".github/.hidden.py", PIN]
    bad = subprocess.run([sys.executable, "-I", str(SCRIPT), "pure", str(listing)], input="\n".join(other) + "\n", capture_output=True, text=True, check=False)
    assert bad.returncode == 1 and bad.stdout.splitlines() == other[:4]


def test_pure_takes_a_file_that_only_the_old_list_names(tmp_path: Path) -> None:
    """A bump removes a file that left the loop: the new list no longer names it, the old list did."""
    new, old = tmp_path / "new.txt", tmp_path / "old.txt"
    new.write_text("agents/\n")
    old.write_text("agents/\ntests/test_gone.py\n")
    run = lambda line, *lists: subprocess.run([sys.executable, "-I", str(SCRIPT), "pure", *map(str, lists)], input=line + "\n",  # noqa: E731
                                              capture_output=True, text=True, check=False)
    assert run("D\ttests/test_gone.py", new).returncode == 1, "the new list alone does not name it"
    assert run("D\ttests/test_gone.py", new, old).returncode == 0
    edited = run("M\ttests/test_gone.py", new, old)
    assert edited.returncode == 1 and edited.stdout == "tests/test_gone.py\n", "an edit of a file that left the list is no bump"
    assert run("tests/test_gone.py", new, old).returncode == 1, "a path with no status is not deleted"


def test_a_copy_that_fails_after_the_branch_exists_names_the_next_step(on_main, tmp_path: Path) -> None:
    """The loop's new commit has no list: the copy fails when the worktree exists already, so the run says what is done."""
    product, environ, _, log = on_main
    origin = tmp_path / "origin"
    (origin / LIST).unlink()
    commit(origin, environ, "the list is gone")
    run = sync(product, environ, "bump")
    tree = product / ".claude" / "worktrees"
    assert run.returncode == 1 and "done so far" in run.stderr and "the copy failed" in run.stderr
    assert "git worktree remove --force" in run.stderr and any(tree.iterdir()), run.stderr
    assert "gh pr create" not in log.read_text()
