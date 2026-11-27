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


def test_bump_refuses_main(world) -> None:
    product, environ, shas = world
    git(product, environ, "switch", "-q", "main")
    run = sync(product, environ, "bump", shas[1])
    assert run.returncode == 1 and "a branch of a worktree" in run.stderr


def test_a_wrong_command_prints_the_usage(world) -> None:
    product, environ, _ = world
    assert sync(product, environ).returncode == 2
    assert sync(product, environ, "bump").returncode == 2


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
